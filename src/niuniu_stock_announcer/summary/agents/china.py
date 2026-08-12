"""China 公告摘要 Agent 与 OpenAI 兼容客户端。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import openai
from openai import OpenAI

from niuniu_stock_announcer.config.settings import AppSettings
from niuniu_stock_announcer.summary.errors import SummaryError
from niuniu_stock_announcer.summary.parser import (
    format_repair_error,
    parse_china_agent_payload,
    should_attempt_repair,
)
from niuniu_stock_announcer.summary.prompts import (
    build_markdown_summary_messages,
    build_repair_messages,
    clone_messages,
    load_system_prompt,
)
from niuniu_stock_announcer.summary.schema import (
    ChinaAgentPayload,
    ChinaSummaryResult,
    SummaryAgentInput,
    SummaryCompletion,
)

SUMMARY_EXTRA_BODY = {"reasoning_split": True}
REASONING_EFFORT = "high"
UNSUPPORTED_PARAM_MARKERS = (
    "unsupported parameter",
    "is not supported",
    "unknown parameter",
    "invalid parameter",
    "unrecognized request argument",
    "reasoning_effort",
    "reasoning_split",
    "extra_body",
    "temperature",
)


@dataclass(frozen=True, slots=True)
class RawSummaryCompletion:
    """只保留 Agent 后续需要的 LLM 内容和 usage，不保留完整 response。"""

    content: str
    input_tokens: int | None
    output_tokens: int | None
    model: str | None


class SummaryLLMClient:
    """封装 Chat Completions 请求与参数兼容降级，不负责业务 JSON 解析。"""

    def __init__(
        self,
        *,
        settings: AppSettings,
        client: OpenAI | None = None,
    ) -> None:
        """绑定显式设置和可选测试替身；客户端按首次请求懒加载。

        Args:
            settings: 由 composition root 注入的应用 LLM 设置。
            client: 可注入的 OpenAI 兼容客户端，测试时避免真实网络。
        """
        self._settings = settings
        self._client = client

    def close(self) -> None:
        """关闭底层 HTTP 客户端，释放批量摘要连接。"""
        if self._client is None:
            return
        self._client.close()
        self._client = None

    def __enter__(self) -> SummaryLLMClient:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def complete(self, messages: Sequence[dict[str, str]]) -> RawSummaryCompletion:
        """执行一轮 Chat Completions 请求并提取最小 typed 结果。

        Args:
            messages: 已由 China Agent 构造的对话消息。

        Returns:
            不含完整响应 JSON 的内容、token 和模型信息。

        Raises:
            SummaryError: 请求失败或模型拒答。
        """
        completion = self._create_completion_with_compat(messages)
        message = completion.choices[0].message
        refusal = getattr(message, "refusal", None)
        if refusal:
            raise SummaryError("REFUSAL", str(refusal))
        return RawSummaryCompletion(
            content=_extract_message_content(message),
            input_tokens=_usage_token(completion, "prompt_tokens"),
            output_tokens=_usage_token(completion, "completion_tokens"),
            model=getattr(completion, "model", None) or self._get_settings().llm_model,
        )

    def _create_completion_with_compat(self, messages: Sequence[dict[str, str]]) -> Any:
        """按旧版固定顺序逐级去除不受支持的请求参数。"""
        levels = (
            (True, True),
            (False, True),
            (False, False),
        )
        for index, (extended, temperature) in enumerate(levels):
            try:
                return self._create_completion(
                    messages,
                    include_extended_options=extended,
                    include_temperature=temperature,
                )
            except openai.OpenAIError as exc:
                if index == len(levels) - 1 or not _is_unsupported_param_error(exc):
                    raise SummaryError("REQUEST", _format_openai_error(exc)) from exc
        raise AssertionError("unreachable completion compatibility branch")

    def _create_completion(
        self,
        messages: Sequence[dict[str, str]],
        *,
        include_extended_options: bool,
        include_temperature: bool,
    ) -> Any:
        """构造并发送 Chat Completions 请求。"""
        settings = self._get_settings()
        request_kwargs: dict[str, Any] = {
            "model": settings.llm_model,
            "messages": clone_messages(messages),
        }
        if include_temperature:
            request_kwargs["temperature"] = settings.llm_temperature
        if include_extended_options:
            request_kwargs["reasoning_effort"] = REASONING_EFFORT
            request_kwargs["extra_body"] = SUMMARY_EXTRA_BODY
        return self._get_client().chat.completions.create(**request_kwargs)

    def _get_client(self) -> OpenAI:
        if self._client is not None:
            return self._client
        settings = self._get_settings()
        self._client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.require_llm_api_key(),
            timeout=settings.llm_timeout,
            max_retries=settings.llm_max_retries,
        )
        return self._client

    def _get_settings(self) -> AppSettings:
        settings = self._settings
        missing = []
        if not settings.llm_base_url:
            missing.append("LLM_BASE_URL")
        if (
            settings.llm_api_key is None
            or not settings.llm_api_key.get_secret_value().strip()
        ):
            missing.append("LLM_API_KEY")
        if not settings.llm_model:
            missing.append("LLM_MODEL")
        if missing:
            raise SummaryError("CONFIG", f"缺少 LLM 配置: {', '.join(missing)}")
        self._settings = settings
        return settings


class SummaryChatClient(Protocol):
    """定义 China Agent 需要的最小 Chat Completions 能力。"""

    def complete(self, messages: Sequence[dict[str, str]]) -> RawSummaryCompletion:
        """执行一轮模型请求并返回不含完整响应的结果。

        Args:
            messages: 由市场 Agent 构造的 Chat Completions 消息。

        Returns:
            响应内容、token 和实际模型名。
        """


class ChinaAnnouncementAgent:
    """组合 China prompt/parser/client 的市场摘要策略。"""

    agent_key = "china-announcement-summary"
    agent_version = "v2"
    prompt_version = "china-announcement-summary.v1"
    model_provider = "openai-compatible"

    def __init__(
        self,
        client: SummaryChatClient,
        *,
        system_prompt: str | None = None,
    ) -> None:
        """绑定已注入的 LLM 客户端与可选测试 prompt。

        Args:
            client: 负责 Chat Completions 和兼容参数降级的客户端。
            system_prompt: 可选显式系统 prompt；省略时由 Agent 懒加载正式 prompt。
        """
        self._client = client
        self._system_prompt = system_prompt

    def summarize(self, request: SummaryAgentInput) -> SummaryCompletion:
        """调用一次或一次修复请求并返回版本化 China 摘要结果。

        Args:
            request: 脱离 Session 的公告 Markdown 与标题输入。

        Returns:
            通过 Pydantic 校验的摘要结果和最小审计字段。

        Raises:
            SummaryError: LLM 请求、拒答、解析或一次修复仍失败。
        """
        messages = build_markdown_summary_messages(self._get_system_prompt(), request)
        completion: RawSummaryCompletion | None = None
        parse_error: SummaryError | None = None
        try:
            completion = self._client.complete(messages)
            payload = parse_china_agent_payload(completion.content)
            return self._build_completion(payload, completion)
        except SummaryError as exc:
            if not should_attempt_repair(exc):
                raise
            parse_error = exc

        repaired = self._client.complete(
            build_repair_messages(
                messages,
                bad_output="" if completion is None else completion.content,
                error_summary=format_repair_error(
                    "" if completion is None else completion.content,
                    parse_error,
                ),
            )
        )
        try:
            payload = parse_china_agent_payload(repaired.content)
        except SummaryError as exc:
            if should_attempt_repair(exc):
                raise SummaryError("REPAIR_EXHAUSTED", exc.message) from exc
            raise
        return self._build_completion(payload, repaired)

    def _get_system_prompt(self) -> str:
        if self._system_prompt is None:
            self._system_prompt = load_system_prompt()
        return self._system_prompt

    def _build_completion(
        self, payload: ChinaAgentPayload, completion: RawSummaryCompletion
    ) -> SummaryCompletion:
        model_name = completion.model or "unknown-model"
        return SummaryCompletion(
            agent_key=self.agent_key,
            agent_version=self.agent_version,
            prompt_version=self.prompt_version,
            model_provider=self.model_provider,
            model_name=model_name,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            result=ChinaSummaryResult(
                summary_text=payload.summary,
                summary_tags=payload.tags,
            ),
        )


def _extract_message_content(message: object) -> str:
    """兼容字符串和分段 content 两种 OpenAI 消息格式。"""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            elif getattr(item, "type", None) == "text":
                parts.append(str(getattr(item, "text", "")))
        return "".join(parts)
    return ""


def _is_unsupported_param_error(exc: Exception) -> bool:
    message = _format_openai_error(exc).lower()
    return any(marker in message for marker in UNSUPPORTED_PARAM_MARKERS)


def _format_openai_error(exc: Exception) -> str:
    message = str(exc).strip()
    return message or exc.__class__.__name__


def _usage_token(completion: object, field_name: str) -> int | None:
    usage = getattr(completion, "usage", None)
    value = None if usage is None else getattr(usage, field_name, None)
    return value if isinstance(value, int) else None
