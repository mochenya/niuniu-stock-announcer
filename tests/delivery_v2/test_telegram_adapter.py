"""Telegram target、sender 结果与 outcome 分类离线测试。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

from niuniu_stock_announcer.im.telegram.schema import (
    TelegramDocumentSendRequest,
    TelegramTextSendRequest,
)
from niuniu_stock_announcer.im.telegram.sender import (
    TelegramSender,
    TelegramSendFailed,
    TelegramSendOutcomeUnknown,
    _call_with_retry,
    _run_async,
)
from niuniu_stock_announcer.im.telegram.run_log import (
    RunLogNotification,
    RunLogStageStats,
    TelegramRunLogNotifier,
    format_run_log_message,
)
from niuniu_stock_announcer.im.telegram.target import (
    parse_run_log_target,
    parse_telegram_topic_url,
)


class _FakeBot:
    instances: list[_FakeBot] = []

    def __init__(self, *, token: str) -> None:
        self.token = token
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def send_message(self, **kwargs):
        self.calls.append(("text", kwargs))
        return SimpleNamespace(
            chat_id=-100123456,
            message_thread_id=9,
            message_id=101,
            link="https://t.me/c/123456/9/101",
        )

    async def send_document(self, **kwargs):
        assert kwargs["document"].read()
        self.calls.append(("document", kwargs))
        return SimpleNamespace(
            chat_id=-100123456,
            message_thread_id=9,
            message_id=202,
            link="https://t.me/c/123456/9/202",
        )


def test_topic_and_run_log_targets_are_parsed_strictly() -> None:
    topic = parse_telegram_topic_url("https://t.me/c/123456/9")
    assert topic.chat_id == -100123456
    assert topic.message_thread_id == 9
    assert parse_run_log_target("@niuniu").chat_id == "@niuniu"
    assert parse_run_log_target("-100987").chat_id == -100987
    assert parse_run_log_target("https://t.me/niuniu").chat_id == "@niuniu"
    assert parse_run_log_target("https://t.me/c/123456/9") == (
        parse_run_log_target("https://telegram.me/c/123456/9")
    )

    for invalid in (
        "",
        "ftp://t.me/c/123/9",
        "https://example.com/c/123/9",
        "https://t.me/c/not-a-number/9",
        "https://t.me/c/123/0",
        "https://t.me/c/123/9/extra",
        "https://t.me/c/123/9?secret=value",
    ):
        with pytest.raises(ValueError):
            parse_telegram_topic_url(invalid)


def test_sender_returns_message_link_and_uses_frozen_payloads(tmp_path: Path) -> None:
    _FakeBot.instances.clear()
    content = b"%PDF-1.7\ncontent\n%%EOF"
    path = tmp_path / "docs/announcement.pdf"
    path.parent.mkdir()
    path.write_bytes(content)
    sender = TelegramSender(
        bot_token="test-token",
        timeout=5,
        document_storage_root=tmp_path,
        bot_factory=_FakeBot,
    )
    target = {"chat_id": -100123456, "message_thread_id": 9}

    text_result = sender.send_text(
        TelegramTextSendRequest(target=target, text_content="<b>摘要</b>")
    )
    document_result = sender.send_document(
        TelegramDocumentSendRequest(
            target=target,
            storage_relative_path="docs/announcement.pdf",
            document_filename="公告.pdf",
            document_size_bytes=len(content),
            document_sha256=hashlib.sha256(content).hexdigest(),
            document_caption="<b>原文</b>",
        )
    )

    assert text_result.message_id == 101
    assert text_result.message_url == "https://t.me/c/123456/9/101"
    assert document_result.message_id == 202
    assert document_result.message_url == "https://t.me/c/123456/9/202"
    assert [bot.calls[0][0] for bot in _FakeBot.instances] == ["text", "document"]
    assert all(bot.token == "test-token" for bot in _FakeBot.instances)


def test_document_validation_fails_before_bot_construction(tmp_path: Path) -> None:
    _FakeBot.instances.clear()
    content = b"%PDF-1.7\ncontent\n%%EOF"
    path = tmp_path / "docs/announcement.pdf"
    path.parent.mkdir()
    path.write_bytes(content)
    sender = TelegramSender(
        bot_token="test-token",
        timeout=5,
        document_storage_root=tmp_path,
        bot_factory=_FakeBot,
    )
    request = TelegramDocumentSendRequest(
        target={"chat_id": -100123456, "message_thread_id": 9},
        storage_relative_path="docs/announcement.pdf",
        document_filename="公告.pdf",
        document_size_bytes=len(content),
        document_sha256="0" * 64,
        document_caption="原文",
    )

    with pytest.raises(TelegramSendFailed, match="发送前校验失败"):
        sender.send_document(request)
    assert _FakeBot.instances == []


@pytest.mark.parametrize("error", [TimedOut("timeout"), NetworkError("network")])
def test_timeout_and_network_are_outcome_unknown(error: Exception) -> None:
    async def fail():
        raise error

    with pytest.raises(TelegramSendOutcomeUnknown):
        _run_async(
            _call_with_retry(
                fail,
                chat_id=-100123456,
                message_thread_id=9,
            )
        )


def test_explicit_telegram_error_is_determined_failure() -> None:
    async def fail():
        raise BadRequest("invalid payload")

    with pytest.raises(TelegramSendFailed, match="明确发送失败"):
        _run_async(
            _call_with_retry(
                fail,
                chat_id=-100123456,
                message_thread_id=9,
            )
        )


def test_retry_after_wait_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PTB_TIMEDELTA", "true")

    async def rate_limited():
        raise RetryAfter(timedelta(seconds=61))

    with pytest.raises(TelegramSendFailed, match="等待时间超出安全上限"):
        _run_async(
            _call_with_retry(
                rate_limited,
                chat_id=-100123456,
                message_thread_id=9,
            )
        )


def test_sender_redacts_bot_token_from_sdk_errors(tmp_path: Path) -> None:
    secret = "123456:super-secret-token"

    class _LeakingBot(_FakeBot):
        async def send_message(self, **kwargs):
            raise BadRequest(f"invalid token {secret}")

    sender = TelegramSender(
        bot_token=secret,
        timeout=5,
        document_storage_root=tmp_path,
        bot_factory=_LeakingBot,
    )

    with pytest.raises(TelegramSendFailed) as error:
        sender.send_text(
            TelegramTextSendRequest(
                target={"chat_id": -100123456, "message_thread_id": 9},
                text_content="摘要",
            )
        )
    assert secret not in str(error.value)
    assert "[REDACTED]" in str(error.value)


def test_sender_redacts_token_from_unclassified_errors(tmp_path: Path) -> None:
    secret = "123456:another-secret-token"

    class _UnexpectedBot(_FakeBot):
        async def send_message(self, **kwargs):
            raise RuntimeError(f"unexpected {secret}")

    sender = TelegramSender(
        bot_token=secret,
        timeout=5,
        document_storage_root=tmp_path,
        bot_factory=_UnexpectedBot,
    )

    with pytest.raises(TelegramSendOutcomeUnknown) as error:
        sender.send_text(
            TelegramTextSendRequest(
                target={"chat_id": -100123456, "message_thread_id": 9},
                text_content="摘要",
            )
        )
    assert secret not in str(error.value)
    assert "[REDACTED]" in str(error.value)


def test_run_log_uses_same_token_and_never_needs_outbox(tmp_path: Path) -> None:
    _FakeBot.instances.clear()
    log_file = tmp_path / "run.log"
    log_file.write_text("full log", encoding="utf-8")
    notification = RunLogNotification(
        command="run <plan>",
        status="warning",
        started_at=datetime(2026, 8, 13, 8, tzinfo=UTC),
        finished_at=datetime(2026, 8, 13, 8, 1, 2, tzinfo=UTC),
        duration_seconds=62,
        log_file=log_file,
        delivery=RunLogStageStats(completed=2, failed=1, unknown=1),
        error="failure <details>",
    )
    notifier = TelegramRunLogNotifier(
        bot_token="global-token",
        target="https://t.me/c/123456/9",
        timeout=5,
        attach_file=True,
        bot_factory=_FakeBot,
    )

    assert notifier.notify(notification) is True
    message = format_run_log_message(notification, will_attach_file=True)
    assert "run &lt;plan&gt;" in message
    assert "失败：<b>1</b>" in message
    assert "未知：<b>1</b>" in message
    assert "failure &lt;details&gt;" in message
    assert _FakeBot.instances[0].token == "global-token"
    assert [event for event, _ in _FakeBot.instances[0].calls] == [
        "text",
        "document",
    ]
