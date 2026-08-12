"""重构前成熟能力的行为特征测试。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest
from cninfo_announcement.models import BusinessAnnouncement
from telegram.error import TelegramError, TimedOut

import announcements.download as download_module
import delivery.telegram.sender as sender_module
from announcements.search import query_search_task
from delivery.telegram.format import (
    format_telegram_pdf_caption,
    format_telegram_summary_text,
)
from delivery.telegram.sender import TelegramSendOutcomeUnknown
from domain.config_models import RuntimeConfig, TelegramChannelConfig, TelegramSettings
from domain.summary_models import AnnouncementSummary, MarkdownSummaryRequest
from domain.search_models import SearchTask
from domain.telegram_models import TelegramSummaryPayload
from filters import combine_keywords, decide_title_filter
from log.events import log_event
from log.formatters import format_fields
from summary.client import SummaryCompletion, SummaryLLMClient
from summary.parser import parse_announcement_summary


class _PdfClient:
    def __init__(self, result: Path) -> None:
        self.result = result
        self.calls: list[tuple[BusinessAnnouncement, Path | None]] = []

    def download_pdf(
        self,
        announcement: BusinessAnnouncement,
        *,
        save_dir: str | Path | None = None,
    ) -> Path:
        path = None if save_dir is None else Path(save_dir)
        self.calls.append((announcement, path))
        return self.result


class _SequenceSummaryClient(SummaryLLMClient):
    def __init__(self, completions: list[SummaryCompletion]) -> None:
        super().__init__()
        self.completions = list(completions)
        self.request_messages: list[list[dict[str, str]]] = []
        self._system_prompt = "测试系统提示"

    def _request_summary_content(
        self, messages: list[dict[str, str]]
    ) -> SummaryCompletion:
        self.request_messages.append([dict(message) for message in messages])
        return self.completions.pop(0)


class _QueryClient:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def query_announcements(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        return object()


class _FakeBot:
    instances: list[_FakeBot] = []

    def __init__(self, *, token: str) -> None:
        self.token = token
        self.events: list[str] = []
        type(self).instances.append(self)

    async def __aenter__(self) -> _FakeBot:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def send_message(self, **_kwargs: object) -> object:
        self.events.append("text")
        return SimpleNamespace(message_id=101)

    async def send_document(self, **_kwargs: object) -> object:
        self.events.append("document")
        return SimpleNamespace(message_id=202)


class _FailingDocumentBot(_FakeBot):
    async def send_document(self, **_kwargs: object) -> object:
        self.events.append("document")
        raise TelegramError("document rejected")


def _announcement(*, source: str = "cninfo") -> BusinessAnnouncement:
    return BusinessAnnouncement(
        source=source,
        sec_code="688090",
        sec_name="瑞松科技",
        org_id="org-1",
        announcement_id="ann-1",
        announcement_title="关于<em>回购</em>股份的公告",
        announcement_time=1785513600000,
        adjunct_url="finalpage/2026-08-01/ann-1.PDF",
    )


def _telegram_config() -> RuntimeConfig:
    channel = TelegramChannelConfig(
        bot_token="bot-token",
        topic_url="https://t.me/c/123456/9",
    )
    return RuntimeConfig(
        watchlist_file=Path("unused.yaml"),
        telegram=TelegramSettings(
            timeout=5,
            a_share=channel,
            hk=channel,
        ),
    )


def _telegram_payload() -> TelegramSummaryPayload:
    return TelegramSummaryPayload(
        source="cninfo",
        announcement_id="ann-1",
        market="sh",
        stock_code="688090",
        stock_key="sh:688090",
        company_name="瑞松科技 & 合伙人",
        announcement=_announcement(),
        summary=AnnouncementSummary(
            summary="公司拟回购股份 <待审议>",
            tags=["回购", "进展", "A股"],
        ),
        search_keyword="回购",
    )


def test_pdf_download_uses_source_subdirectory_and_injected_client(
    tmp_path: Path,
) -> None:
    expected = tmp_path / "cninfo" / "ann-1.pdf"
    client = _PdfClient(expected)

    result = download_module.download_announcement_pdf(
        _announcement(),
        save_dir=tmp_path,
        client=client,
    )

    assert result == expected
    assert client.calls == [(_announcement(), tmp_path / "cninfo")]


@pytest.mark.parametrize(
    ("source", "market", "expected_args"),
    [
        ("cninfo", "sh", ("sh",)),
        ("sse", "sh", ()),
        ("szse", "sz", ()),
    ],
)
def test_provider_query_adapter_preserves_exact_arguments(
    source: str, market: str, expected_args: tuple[str, ...]
) -> None:
    client = _QueryClient()
    task = SearchTask(
        announcement_source=source,
        source_key=f"{source}::{market}::stock_keyword::600000::回购",
        market=market,
        stock_code="600000",
        stock_key=f"{market}:600000",
        search_mode="stock_keyword",
        search_keyword="回购",
    )

    query_search_task(
        client,
        task,
        start_date=date(2026, 8, 11),
        end_date=date(2026, 8, 12),
    )

    assert client.calls == [
        (
            expected_args,
            {
                "stock": "600000",
                "start_date": date(2026, 8, 11),
                "end_date": date(2026, 8, 12),
                "searchkey": "回购",
            },
        )
    ]


@pytest.mark.parametrize(
    ("source", "downloader_name"),
    [
        ("cninfo", "download_cninfo_pdf"),
        ("sse", "download_sse_pdf"),
        ("szse", "download_szse_pdf"),
    ],
)
def test_pdf_download_routes_each_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source: str,
    downloader_name: str,
) -> None:
    calls: list[Path | None] = []

    def downloader(
        _announcement: BusinessAnnouncement,
        *,
        save_dir: str | Path | None = None,
    ) -> Path:
        calls.append(None if save_dir is None else Path(save_dir))
        return tmp_path / source / "result.pdf"

    monkeypatch.setattr(download_module, downloader_name, downloader)

    result = download_module.download_announcement_pdf(
        _announcement(source=source),
        save_dir=tmp_path,
    )

    assert result == tmp_path / source / "result.pdf"
    assert calls == [tmp_path / source]


def test_title_filter_and_keyword_combination_preserve_config_order() -> None:
    keywords = combine_keywords(
        ["减持", " 回购 ", ""],
        ["回购", "更正", "减持"],
    )
    decision = decide_title_filter("关于回购股份及更正的公告", keywords)

    assert keywords == ["减持", "回购", "更正"]
    assert decision.filtered is True
    assert decision.reason == "title_exclude_keyword"
    assert decision.matched_keywords == ["回购", "更正"]


def test_summary_parser_unwraps_code_fence_and_validates_tags() -> None:
    summary = parse_announcement_summary(
        '\ufeff```json\n{"summary":"摘要", "tags":["回购","进展","A股"]}\n```'
    )

    assert summary.summary == "摘要"
    assert summary.tags == ["回购", "进展", "A股"]


def test_summary_client_repairs_invalid_json_exactly_once() -> None:
    client = _SequenceSummaryClient(
        [
            SummaryCompletion(
                content="not-json",
                response_json=None,
                input_tokens=None,
                output_tokens=None,
                model="test-model",
            ),
            SummaryCompletion(
                content='{"summary":"修复后的摘要", "tags":["回购","进展","A股"]}',
                response_json={"id": "response-2"},
                input_tokens=20,
                output_tokens=8,
                model="test-model",
            ),
        ]
    )

    result = client.summarize_markdown(
        MarkdownSummaryRequest(
            announcement_id="ann-1",
            company_name="瑞松科技",
            announcement_title="回购公告",
            markdown="公告正文",
        )
    )

    assert result.summary.summary == "修复后的摘要"
    assert result.llm_response_json == {"id": "response-2"}
    assert len(client.request_messages) == 2
    assert client.request_messages[1][-2]["role"] == "assistant"
    assert client.request_messages[1][-2]["content"] == "not-json"


def test_llm_client_drops_unsupported_options_in_fixed_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = httpx.Response(
        400,
        request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"),
    )
    errors = [
        openai.BadRequestError(
            "unsupported parameter reasoning_effort",
            response=response,
            body=None,
        ),
        openai.BadRequestError(
            "unsupported parameter temperature",
            response=response,
            body=None,
        ),
    ]
    attempts: list[tuple[bool, bool]] = []
    expected_result = object()
    client = SummaryLLMClient()

    def create(
        _messages: object,
        *,
        include_extended_options: bool,
        include_temperature: bool,
    ) -> object:
        attempts.append((include_extended_options, include_temperature))
        if errors:
            raise errors.pop(0)
        return expected_result

    monkeypatch.setattr(client, "_create_completion", create)

    result = client._create_completion_with_compat(
        [{"role": "user", "content": "test"}]
    )

    assert result is expected_result
    assert attempts == [(True, True), (False, True), (False, False)]


def test_telegram_formatter_escapes_content_and_keeps_stable_header() -> None:
    payload = _telegram_payload()

    text = format_telegram_summary_text(payload)
    caption = format_telegram_pdf_caption(payload)

    assert "关于回购股份的公告" in text
    assert "瑞松科技 &amp; 合伙人" in text
    assert "公司拟回购股份 &lt;待审议&gt;" in text
    assert "#回购 #进展 #A股" in text
    assert caption == "\n".join(text.splitlines()[:5])


def test_telegram_sends_text_before_document_and_calls_each_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "announcement.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n%%EOF")
    callbacks: list[tuple[str, int]] = []
    _FakeBot.instances.clear()
    monkeypatch.setattr(sender_module, "Bot", _FakeBot)

    result = sender_module.send_telegram_delivery(
        _telegram_payload(),
        pdf_path,
        send_text=True,
        send_pdf=True,
        config=_telegram_config(),
        on_text_sent=lambda item: callbacks.append((item.kind, item.message_id)),
        on_pdf_sent=lambda item: callbacks.append((item.kind, item.message_id)),
    )

    assert _FakeBot.instances[0].events == ["text", "document"]
    assert callbacks == [("text", 101), ("document", 202)]
    assert result.text is not None and result.text.message_id == 101
    assert result.pdf is not None and result.pdf.message_id == 202


def test_telegram_document_failure_keeps_confirmed_text_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "announcement.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n%%EOF")
    callbacks: list[tuple[str, int]] = []
    _FailingDocumentBot.instances.clear()
    monkeypatch.setattr(sender_module, "Bot", _FailingDocumentBot)

    with pytest.raises(RuntimeError, match="Telegram send failed"):
        sender_module.send_telegram_delivery(
            _telegram_payload(),
            pdf_path,
            send_text=True,
            send_pdf=True,
            config=_telegram_config(),
            on_text_sent=lambda item: callbacks.append((item.kind, item.message_id)),
            on_pdf_sent=lambda item: callbacks.append((item.kind, item.message_id)),
        )

    assert _FailingDocumentBot.instances[0].events == ["text", "document"]
    assert callbacks == [("text", 101)]


def test_telegram_timeout_is_outcome_unknown() -> None:
    async def timed_out() -> object:
        raise TimedOut("timeout")

    with pytest.raises(TelegramSendOutcomeUnknown):
        sender_module._run_async(
            sender_module._call_with_retry(
                timed_out,
                chat_id=-100123456,
                message_thread_id=9,
            )
        )


def test_structured_log_keeps_field_order_and_only_console_truncates_title() -> None:
    title = "很长的公告标题" * 20
    event = log_event(
        "sync",
        "selected",
        source="cninfo",
        ann_id="ann-1",
        title=title,
        custom="value",
    )

    console = format_fields(event.fields, truncate=True)
    file_text = format_fields(event.fields, truncate=False)

    assert console.index("source=") < console.index("ann_id=") < console.index("title=")
    assert console.endswith("custom=value")
    assert title not in console
    assert f"title={title}" in file_text
