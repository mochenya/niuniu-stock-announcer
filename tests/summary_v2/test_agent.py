"""v2 China Agent 的 parser、prompt、兼容降级与一次修复测试。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import httpx
import openai
import pytest

from niuniu_stock_announcer.config.settings import AppSettings
from niuniu_stock_announcer.summary.agents.china import (
    ChinaAnnouncementAgent,
    RawSummaryCompletion,
    SummaryLLMClient,
)
from niuniu_stock_announcer.summary.errors import SummaryError
from niuniu_stock_announcer.summary.parser import parse_china_agent_payload
from niuniu_stock_announcer.summary.prompts import (
    build_summary_user_content,
    load_system_prompt,
)
from niuniu_stock_announcer.summary.schema import SummaryAgentInput


class _SequenceClient:
    def __init__(self, completions: list[RawSummaryCompletion]) -> None:
        self.completions = list(completions)
        self.messages: list[list[dict[str, str]]] = []

    def complete(self, messages: Sequence[dict[str, str]]) -> RawSummaryCompletion:
        self.messages.append([dict(message) for message in messages])
        return self.completions.pop(0)


def _request() -> SummaryAgentInput:
    return SummaryAgentInput(
        announcement_id="ann-1",
        company_name="瑞松科技",
        announcement_title="回购公告",
        markdown="公司已回购股份。",
    )


def _completion(content: str) -> RawSummaryCompletion:
    return RawSummaryCompletion(
        content=content,
        input_tokens=20,
        output_tokens=8,
        model="test-model",
    )


def _settings() -> AppSettings:
    return AppSettings(
        _env_file=None,
        LLM_BASE_URL="https://example.invalid/v1",
        LLM_API_KEY="test-secret",
        LLM_MODEL="test-model",
    )


def test_parser_preserves_fenced_json_and_three_to_six_tag_contract() -> None:
    payload = parse_china_agent_payload(
        '\ufeff```json\n{"summary":" 摘要 ","tags":["回购","进展","A股"]}\n```'
    )

    assert payload.summary == "摘要"
    assert payload.tags == ("回购", "进展", "A股")
    with pytest.raises(SummaryError) as error:
        parse_china_agent_payload('{"summary":"摘要","tags":["回购"]}')
    assert error.value.code == "PARSE_SCHEMA"


def test_migrated_prompt_and_user_content_preserve_v1_contract() -> None:
    legacy_prompt = Path(
        "src/niuniu_stock_announcer/summary/prompts/announcement_summary.md"
    ).read_text(encoding="utf-8")

    assert load_system_prompt() == legacy_prompt.strip()
    user_content = build_summary_user_content(_request())
    assert "公司 瑞松科技" in user_content
    assert "回购公告" in user_content
    assert "json.loads" in user_content
    assert user_content.endswith("公告原文是：\n公司已回购股份。")


def test_china_agent_repairs_invalid_json_exactly_once_without_raw_response() -> None:
    client = _SequenceClient(
        [
            _completion("not-json"),
            _completion('{"summary":"修复后的摘要","tags":["回购","进展","A股"]}'),
        ]
    )
    agent = ChinaAnnouncementAgent(client, system_prompt="测试系统提示")

    result = agent.summarize(_request())

    assert result.result.summary_text == "修复后的摘要"
    assert result.result.summary_tags == ("回购", "进展", "A股")
    assert result.model_dump().keys() == {
        "agent_key",
        "agent_version",
        "prompt_version",
        "model_provider",
        "model_name",
        "input_tokens",
        "output_tokens",
        "result",
    }
    assert len(client.messages) == 2
    assert client.messages[1][-2] == {"role": "assistant", "content": "not-json"}


def test_china_agent_never_attempts_a_second_repair() -> None:
    client = _SequenceClient([_completion("bad-1"), _completion("bad-2")])
    agent = ChinaAnnouncementAgent(client, system_prompt="测试系统提示")

    with pytest.raises(SummaryError) as error:
        agent.summarize(_request())

    assert error.value.code == "REPAIR_EXHAUSTED"
    assert len(client.messages) == 2


def test_llm_client_drops_unsupported_options_in_fixed_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = httpx.Response(
        400,
        request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"),
    )
    errors = [
        openai.BadRequestError(
            "unsupported parameter reasoning_effort", response=response, body=None
        ),
        openai.BadRequestError(
            "unsupported parameter temperature", response=response, body=None
        ),
    ]
    attempts: list[tuple[bool, bool]] = []
    expected = object()
    client = SummaryLLMClient(settings=_settings())

    def create(
        _messages: object,
        *,
        include_extended_options: bool,
        include_temperature: bool,
    ) -> object:
        attempts.append((include_extended_options, include_temperature))
        if errors:
            raise errors.pop(0)
        return expected

    monkeypatch.setattr(client, "_create_completion", create)

    result = client._create_completion_with_compat(
        [{"role": "user", "content": "test"}]
    )

    assert result is expected
    assert attempts == [(True, True), (False, True), (False, False)]


def test_non_parameter_openai_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = httpx.Response(
        500,
        request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"),
    )
    client = SummaryLLMClient(settings=_settings())
    calls = 0

    def create(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise openai.InternalServerError(
            "provider unavailable", response=response, body=None
        )

    monkeypatch.setattr(client, "_create_completion", create)

    with pytest.raises(SummaryError) as error:
        client._create_completion_with_compat([{"role": "user", "content": "test"}])

    assert error.value.code == "REQUEST"
    assert calls == 1
