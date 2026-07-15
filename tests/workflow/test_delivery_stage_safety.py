from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from cninfo_announcement.models import BusinessAnnouncement

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import workflow.delivery_stage as delivery_stage  # noqa: E402
import workflow.pending as pending_module  # noqa: E402
from domain.config_models import RuntimeConfig, StockConfig, WatchlistConfig  # noqa: E402
from domain.telegram_models import (  # noqa: E402
    TelegramDeliveryResult,
    TelegramSendResult,
)
from db.records import DeliveryCandidateRecord  # noqa: E402


class _FakeConnection:
    def __init__(self, *, fail_commit_numbers: set[int] | None = None) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit_numbers = fail_commit_numbers or set()

    def commit(self) -> None:
        self.commits += 1
        if self.commits in self.fail_commit_numbers:
            raise RuntimeError(f"commit {self.commits} failed")

    def rollback(self) -> None:
        self.rollbacks += 1


class _FakeDeliveryRepository:
    def __init__(
        self,
        *,
        fail_text_save: bool = False,
        fail_pdf_save: bool = False,
    ) -> None:
        self.fail_text_save = fail_text_save
        self.fail_pdf_save = fail_pdf_save
        self.running_ids: list[int] = []
        self.text_messages: list[dict[str, int]] = []
        self.pdf_messages: list[dict[str, int]] = []
        self.completed_ids: list[int] = []
        self.failures: list[dict[str, str | int]] = []

    def mark_delivery_running(self, *, delivery_id: int) -> None:
        self.running_ids.append(delivery_id)

    def save_text_message(
        self,
        *,
        delivery_id: int,
        chat_id: int,
        message_thread_id: int,
        message_id: int,
    ) -> None:
        if self.fail_text_save:
            raise RuntimeError("text message_id save failed")
        self.text_messages.append(
            {
                "delivery_id": delivery_id,
                "chat_id": chat_id,
                "message_thread_id": message_thread_id,
                "message_id": message_id,
            }
        )

    def save_pdf_message(
        self,
        *,
        delivery_id: int,
        chat_id: int,
        message_thread_id: int,
        message_id: int,
    ) -> None:
        if self.fail_pdf_save:
            raise RuntimeError("pdf message_id save failed")
        self.pdf_messages.append(
            {
                "delivery_id": delivery_id,
                "chat_id": chat_id,
                "message_thread_id": message_thread_id,
                "message_id": message_id,
            }
        )

    def mark_delivery_completed(self, *, delivery_id: int) -> None:
        self.completed_ids.append(delivery_id)

    def save_delivery_failure(
        self,
        *,
        delivery_id: int,
        status: str,
        failure_reason: str,
        failure_log: str,
    ) -> None:
        self.failures.append(
            {
                "delivery_id": delivery_id,
                "status": status,
                "failure_reason": failure_reason,
                "failure_log": failure_log,
            }
        )


class _RetryFailedRepository:
    def __init__(self) -> None:
        self.claim_statuses: list[tuple[str, ...]] = []

    def reset_stale_running_deliveries(self, *, timeout_minutes: int) -> int:
        assert timeout_minutes == 30
        return 0

    def claim_delivery_candidates(
        self,
        *,
        statuses: tuple[str, ...],
        limit: int | None = None,
    ) -> list[DeliveryCandidateRecord]:
        assert limit is None
        self.claim_statuses.append(tuple(statuses))
        return []


def _build_candidate(*, sent_kind: str) -> DeliveryCandidateRecord:
    return DeliveryCandidateRecord(
        source="cninfo",
        announcement_id="ann-1",
        announcement=BusinessAnnouncement(
            source="cninfo",
            sec_code="600000",
            sec_name="测试公司",
            announcement_id="ann-1",
            announcement_title="测试公告",
            announcement_time=1,
        ),
        market="sh",
        stock_code="600000",
        stock_key="sh:600000",
        company_name="测试公司",
        summary_status="completed",
        pdf_local_path=Path("/tmp/ann-1.pdf"),
        summary_text="测试摘要",
        summary_tags=["标签一", "标签二", "标签三"],
        delivery_id=1,
        target_key="a_share",
        text_message_id=101 if sent_kind == "pdf" else None,
    )


def _runtime_config() -> RuntimeConfig:
    return RuntimeConfig(watchlist_file=Path("unused.yaml"))


def _watchlist_config() -> WatchlistConfig:
    return WatchlistConfig(
        stocks=[StockConfig(market="sh", code="600000")],
    )


def _install_fake_sender(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, bool]]:
    calls: list[dict[str, bool]] = []

    def fake_send_telegram_delivery(
        _payload,
        *,
        pdf_path,
        send_text: bool,
        send_pdf: bool,
        config=None,
        on_text_sent=None,
        on_pdf_sent=None,
    ) -> TelegramDeliveryResult:
        assert pdf_path == Path("/tmp/ann-1.pdf")
        assert config is not None
        calls.append({"send_text": send_text, "send_pdf": send_pdf})
        text_result = None
        if send_text:
            text_result = TelegramSendResult(
                announcement_id="ann-1",
                kind="text",
                chat_id=123,
                message_thread_id=456,
                message_id=1001,
            )
            if on_text_sent is not None:
                on_text_sent(text_result)
        pdf_result = None
        if send_pdf:
            pdf_result = TelegramSendResult(
                announcement_id="ann-1",
                kind="document",
                chat_id=123,
                message_thread_id=456,
                message_id=2001,
            )
            if on_pdf_sent is not None:
                on_pdf_sent(pdf_result)
        return TelegramDeliveryResult(text=text_result, pdf=pdf_result)

    monkeypatch.setattr(
        delivery_stage,
        "send_telegram_delivery",
        fake_send_telegram_delivery,
    )
    return calls


@pytest.mark.parametrize(
    ("sent_kind", "failure_mode"),
    [
        ("text", "save"),
        ("text", "commit"),
        ("pdf", "save"),
        ("pdf", "commit"),
    ],
)
def test_post_send_message_id_persistence_failures_mark_delivery_unknown(
    monkeypatch: pytest.MonkeyPatch,
    sent_kind: str,
    failure_mode: str,
) -> None:
    repo = _FakeDeliveryRepository(
        fail_text_save=sent_kind == "text" and failure_mode == "save",
        fail_pdf_save=sent_kind == "pdf" and failure_mode == "save",
    )
    conn = _FakeConnection(
        fail_commit_numbers={2} if failure_mode == "commit" else None,
    )
    sender_calls = _install_fake_sender(monkeypatch)
    events = []

    summary = delivery_stage.run_delivery_candidates(
        repo,
        conn=conn,
        candidates=[_build_candidate(sent_kind=sent_kind)],
        runtime_config=_runtime_config(),
        watchlist_config=_watchlist_config(),
        progress=events.append,
    )

    assert summary.completed_count == 0
    assert summary.failed_count == 0
    assert summary.unknown_count == 1
    assert repo.running_ids == [1]
    assert repo.completed_ids == []
    assert repo.failures[-1]["status"] == "unknown"
    assert "message_id" in str(repo.failures[-1]["failure_reason"])
    assert conn.rollbacks == 1
    assert sender_calls == [
        {
            "send_text": sent_kind == "text",
            "send_pdf": True,
        }
    ]
    assert [event.event for event in events].count("unknown") == 1


def test_retry_failed_delivery_claims_failed_but_not_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _RetryFailedRepository()
    conn = _FakeConnection()

    @contextmanager
    def fake_open_workflow_resources(**_kwargs):
        yield SimpleNamespace(
            runtime_config=SimpleNamespace(delivery_running_timeout_minutes=30),
            conn=conn,
            repo=repo,
            watchlist_config=object(),
        )

    monkeypatch.setattr(
        pending_module,
        "_open_workflow_resources",
        fake_open_workflow_resources,
    )

    result = pending_module.retry_failed_deliveries(progress=lambda _event: None)

    assert result.candidate_count == 0
    assert repo.claim_statuses == [("failed",)]
    assert all("unknown" not in statuses for statuses in repo.claim_statuses)
