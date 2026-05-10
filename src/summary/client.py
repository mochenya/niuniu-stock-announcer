from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import openai
from openai import OpenAI

from config.runtime import load_runtime_config
from domain.config_models import RuntimeConfig
from domain.summary_models import (
    AnnouncementSummary,
    MarkdownSummaryRequest,
    SummaryRunResult,
)
from summary.errors import SummaryError
from summary.parser import (
    format_repair_error,
    parse_announcement_summary,
    should_attempt_repair,
)
from summary.prompts import (
    build_markdown_summary_messages,
    build_repair_messages,
    clone_messages,
    load_system_prompt,
)

SUMMARY_EXTRA_BODY = {"reasoning_split": True}
REASONING_EFFORT = "high"
UNSUPPORTED_REQUEST_PARAM_MARKERS = (
    "reasoning_effort",
    "reasoning_split",
    "extra_body",
)


@dataclass(frozen=True)
class SummaryCompletion:
    """保留一次 LLM 响应中后续落库需要的摘要信息。"""

    content: str
    response_json: dict[str, object] | None
    input_tokens: int | None
    output_tokens: int | None
    model: str | None


class SummaryLLMClient:
    """封装公告摘要的 LLM 请求、解析和一次格式修复。"""

    def __init__(self, *, config: RuntimeConfig | None = None) -> None:
        self._config = config
        self._client: OpenAI | None = None
        self._system_prompt: str | None = None

    def close(self) -> None:
        """关闭底层 OpenAI HTTP 客户端，供 workflow 批量处理结束时释放连接。"""
        if self._client is None:
            return
        self._client.close()
        self._client = None

    def __enter__(self) -> SummaryLLMClient:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def summarize_markdown(self, request: MarkdownSummaryRequest) -> SummaryRunResult:
        """生成公告摘要；若输出格式可修复，则追加一次修复请求。"""
        summary_messages = build_markdown_summary_messages(
            self._get_system_prompt(), request
        )
        completion: SummaryCompletion | None = None
        parse_error: SummaryError | None = None
        try:
            completion = self._request_summary_content(summary_messages)
            summary = parse_announcement_summary(completion.content)
            return _build_run_result(request, summary, completion)
        except SummaryError as exc:
            if not should_attempt_repair(exc):
                raise
            parse_error = exc

        # 只修复 JSON 格式和字段结构，不重新解释公告内容，避免第二轮引入新事实。
        repaired_completion = self._request_summary_content(
            build_repair_messages(
                summary_messages,
                bad_output="" if completion is None else completion.content,
                error_summary=format_repair_error(
                    "" if completion is None else completion.content,
                    parse_error,
                ),
            )
        )
        try:
            summary = parse_announcement_summary(repaired_completion.content)
            return _build_run_result(request, summary, repaired_completion)
        except SummaryError as repair_exc:
            if should_attempt_repair(repair_exc):
                raise SummaryError(
                    "REPAIR_EXHAUSTED", repair_exc.message
                ) from repair_exc
            raise

    def _request_summary_content(
        self,
        messages: Sequence[dict[str, str]],
    ) -> SummaryCompletion:
        """请求一次 LLM 输出，并提取落库需要的内容、token 和原始响应。"""
        completion = self._create_completion_with_compat(messages)
        message = completion.choices[0].message
        refusal = getattr(message, "refusal", None)
        if refusal:
            raise SummaryError("REFUSAL", str(refusal))
        content = _extract_message_content(message)
        return SummaryCompletion(
            content=content,
            response_json=_completion_to_json(completion),
            input_tokens=_usage_token(completion, "prompt_tokens"),
            output_tokens=_usage_token(completion, "completion_tokens"),
            model=getattr(completion, "model", None),
        )

    def _create_completion_with_compat(self, messages: Sequence[dict[str, str]]) -> Any:
        """优先使用扩展参数请求；兼容服务不支持时自动降级重试一次。"""
        try:
            return self._create_completion(messages, include_extended_options=True)
        except openai.OpenAIError as exc:
            if not _is_unsupported_request_param_error(exc):
                raise SummaryError("REQUEST", _format_openai_error(exc)) from exc
            # 兼容不支持 reasoning_effort 或 extra_body 的 OpenAI 兼容服务。
            try:
                return self._create_completion(messages, include_extended_options=False)
            except openai.OpenAIError as fallback_exc:
                raise SummaryError(
                    "REQUEST", _format_openai_error(fallback_exc)
                ) from fallback_exc

    def _create_completion(
        self,
        messages: Sequence[dict[str, str]],
        *,
        include_extended_options: bool,
    ) -> Any:
        """构造并发送 Chat Completions 请求。"""
        config = self._get_config()
        request_kwargs: dict[str, Any] = {
            "model": config.llm_model,
            "temperature": config.llm_temperature,
            "messages": clone_messages(messages),
        }
        if include_extended_options:
            request_kwargs["reasoning_effort"] = REASONING_EFFORT
            request_kwargs["extra_body"] = SUMMARY_EXTRA_BODY
        return self._get_client().chat.completions.create(**request_kwargs)

    def _get_client(self) -> OpenAI:
        """懒加载 OpenAI 客户端，保证同一批摘要复用连接配置。"""
        if self._client is not None:
            return self._client
        config = self._get_config()
        self._client = OpenAI(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            timeout=config.llm_timeout,
            max_retries=config.llm_max_retries,
        )
        return self._client

    def _get_config(self) -> RuntimeConfig:
        """加载并校验 LLM 配置，把配置错误统一转换为 SummaryError。"""
        try:
            config = self._config or load_runtime_config(require_llm=True)
            _require_llm_config(config)
        except ValueError as exc:
            raise SummaryError("CONFIG", str(exc)) from exc
        self._config = config
        return config

    def _get_system_prompt(self) -> str:
        """懒加载系统 prompt，避免批量摘要时重复读文件。"""
        if self._system_prompt is None:
            self._system_prompt = load_system_prompt()
        return self._system_prompt


def _build_run_result(
    request: MarkdownSummaryRequest,
    summary: AnnouncementSummary,
    completion: SummaryCompletion,
) -> SummaryRunResult:
    return SummaryRunResult(
        announcement_id=request.announcement_id,
        summary=summary,
        llm_model=completion.model,
        llm_response_json=completion.response_json,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
    )


def _extract_message_content(message: object) -> str:
    """兼容字符串和分段 content 两种 OpenAI 消息格式。"""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        raw_content = content
    elif isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                continue
            if getattr(item, "type", None) == "text":
                text_parts.append(getattr(item, "text", ""))
        raw_content = "".join(text_parts)
    else:
        raw_content = ""
    if not raw_content.strip():
        raise SummaryError("PARSE_EMPTY", "LLM did not return JSON content")
    return raw_content


def _is_unsupported_request_param_error(exc: Exception) -> bool:
    message = _format_openai_error(exc).lower()
    return any(marker in message for marker in UNSUPPORTED_REQUEST_PARAM_MARKERS)


def _format_openai_error(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    return message


def _require_llm_config(config: RuntimeConfig) -> None:
    missing_fields = [
        name
        for name, value in (
            ("LLM_BASE_URL", config.llm_base_url),
            ("LLM_API_KEY", config.llm_api_key),
            ("LLM_MODEL", config.llm_model),
        )
        if not value
    ]
    if missing_fields:
        raise ValueError(f"missing LLM config: {', '.join(missing_fields)}")


def _completion_to_json(completion: object) -> dict[str, object] | None:
    if hasattr(completion, "model_dump"):
        payload = completion.model_dump(mode="json")
        return payload if isinstance(payload, dict) else None
    return None


def _usage_token(completion: object, field_name: str) -> int | None:
    usage = getattr(completion, "usage", None)
    value = None if usage is None else getattr(usage, field_name, None)
    return value if isinstance(value, int) else None
