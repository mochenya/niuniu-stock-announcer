"""生产 Delivery materializer 与 Telegram Stage 的 PostgreSQL 集成测试。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from niuniu_stock_announcer.db.connection import create_session_factory
from niuniu_stock_announcer.db.model import (
    TelegramDocumentMessageModel,
    TelegramSummaryMessageModel,
)
from niuniu_stock_announcer.db.schema import PdfSnapshot
from niuniu_stock_announcer.db.unit_of_work import UnitOfWork
from niuniu_stock_announcer.delivery.service import ChinaDeliveryMaterializer
from niuniu_stock_announcer.im.telegram.schema import TelegramSendResult
from niuniu_stock_announcer.im.telegram.sender import (
    TelegramSendFailed,
    TelegramSendOutcomeUnknown,
)
from niuniu_stock_announcer.pipelines.china.stages.delivery import DeliveryStage
from tests.db_v2.factories import (
    announcement,
    delivery,
    selected_match,
    summary_completion,
)

pytestmark = pytest.mark.postgres


class _FakeSender:
    def __init__(
        self,
        *,
        text_errors: list[Exception] | None = None,
        document_errors: list[Exception] | None = None,
        on_text=None,
        on_document=None,
    ) -> None:
        self._text_errors = list(text_errors or [])
        self._document_errors = list(document_errors or [])
        self._on_text = on_text
        self._on_document = on_document
        self.events: list[str] = []
        self.requests: list[object] = []

    def send_text(self, request):
        self.events.append("text")
        self.requests.append(request)
        if self._on_text is not None:
            self._on_text(request)
        if self._text_errors:
            raise self._text_errors.pop(0)
        return TelegramSendResult(
            chat_id=request.target.chat_id,
            message_thread_id=request.target.message_thread_id,
            message_id=101,
            message_url="https://t.me/c/1234567890/100/101",
        )

    def send_document(self, request):
        self.events.append("document")
        self.requests.append(request)
        if self._on_document is not None:
            self._on_document(request)
        if self._document_errors:
            raise self._document_errors.pop(0)
        return TelegramSendResult(
            chat_id=request.target.chat_id,
            message_thread_id=request.target.message_thread_id,
            message_id=202,
            message_url="https://t.me/c/1234567890/100/202",
        )


class _CommitThenRaiseContext:
    def __init__(self, session_factory, *, fail: bool) -> None:
        self._inner = UnitOfWork(session_factory)
        self._fail = fail

    def __enter__(self) -> UnitOfWork:
        return self._inner.__enter__()

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        result = self._inner.__exit__(exc_type, exc_value, traceback)
        if exc_type is None and self._fail:
            raise RuntimeError("simulated lost commit response")
        return result


class _CommitThenRaiseFactory:
    def __init__(self, session_factory, *, fail_calls: set[int]) -> None:
        self._session_factory = session_factory
        self._fail_calls = fail_calls
        self.calls = 0

    def __call__(self) -> _CommitThenRaiseContext:
        self.calls += 1
        return _CommitThenRaiseContext(
            self._session_factory,
            fail=self.calls in self._fail_calls,
        )


def _materialize_completed(
    session_factory,
    *,
    identity: str,
    send_original_document: bool,
    content: bytes,
) -> tuple[int, int, int]:
    sha256 = hashlib.sha256(content).hexdigest()
    with UnitOfWork(session_factory) as uow:
        record = uow.china_announcements.upsert(announcement(identity))
        uow.china_announcements.attach_pdf(
            record.id,
            PdfSnapshot(
                storage_relative_path=f"cninfo/2026/08/{identity}.pdf",
                size_bytes=len(content),
                sha256=sha256,
            ),
        )
        uow.china_matches.record(selected_match(record.id))
        summary = uow.china_summaries.ensure(record.id)
        parent = uow.telegram.ensure_delivery(
            delivery(
                summary.id,
                send_original_document=send_original_document,
            )
        )
        announcement_id = record.id
        summary_id = summary.id
        delivery_id = parent.id
    with UnitOfWork(session_factory) as uow:
        claim = uow.china_summaries.claim_next(summary_ids=(summary_id,))
        assert claim is not None
    with UnitOfWork(session_factory) as uow:
        uow.china_summaries.lock(summary_id)
        uow.china_summaries.save_completed(summary_id, summary_completion())
        ChinaDeliveryMaterializer()(uow, summary_id, delivery_id)
    return announcement_id, summary_id, delivery_id


def _materialize_skipped(
    session_factory,
    *,
    identity: str,
    content: bytes,
) -> tuple[int, int]:
    with UnitOfWork(session_factory) as uow:
        record = uow.china_announcements.upsert(announcement(identity))
        uow.china_announcements.attach_pdf(
            record.id,
            PdfSnapshot(
                storage_relative_path=f"cninfo/2026/08/{identity}.pdf",
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            ),
        )
        uow.china_matches.record(selected_match(record.id))
        summary = uow.china_summaries.ensure(record.id)
        parent = uow.telegram.ensure_delivery(
            delivery(summary.id, send_original_document=False)
        )
        summary_id = summary.id
        delivery_id = parent.id
    with UnitOfWork(session_factory) as uow:
        assert uow.china_summaries.claim_next(summary_ids=(summary_id,)) is not None
    with UnitOfWork(session_factory) as uow:
        uow.china_summaries.save_failed(
            summary_id,
            reason="Agent 失败",
            failure_log="controlled failure",
        )
    with UnitOfWork(session_factory) as uow:
        uow.china_summaries.lock(summary_id)
        uow.china_summaries.save_skipped(
            summary_id,
            reason="摘要重试次数已达到上限",
            minimum_failure_count=1,
        )
        ChinaDeliveryMaterializer()(uow, summary_id, delivery_id)
    return summary_id, delivery_id


def test_materializer_obeys_document_intent_and_skipped_forces_pdf(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    content = b"%PDF-1.7\nmaterialized\n%%EOF"
    _, summary_id, delivery_id = _materialize_completed(
        session_factory,
        identity="no-original",
        send_original_document=False,
        content=content,
    )
    with UnitOfWork(session_factory) as uow:
        uow.china_summaries.lock(summary_id)
        ChinaDeliveryMaterializer()(uow, summary_id, delivery_id)

    _, skipped_delivery_id = _materialize_skipped(
        session_factory,
        identity="skipped-original",
        content=content,
    )
    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(TelegramSummaryMessageModel)
                .where(TelegramSummaryMessageModel.telegram_delivery_id == delivery_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(TelegramDocumentMessageModel)
                .where(TelegramDocumentMessageModel.telegram_delivery_id == delivery_id)
            )
            == 0
        )
        skipped_document = session.scalar(
            select(TelegramDocumentMessageModel).where(
                TelegramDocumentMessageModel.telegram_delivery_id == skipped_delivery_id
            )
        )
        assert skipped_document is not None


def test_stage_commits_claim_sends_text_first_and_saves_each_link(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    content = b"%PDF-1.7\nstage-success\n%%EOF"
    _, _, delivery_id = _materialize_completed(
        session_factory,
        identity="stage-success",
        send_original_document=True,
        content=content,
    )

    def assert_text_claim_committed(_request) -> None:
        with Session(postgres_engine) as session:
            assert (
                session.scalar(select(TelegramSummaryMessageModel.status)) == "running"
            )

    def assert_text_sent_before_document(_request) -> None:
        with Session(postgres_engine) as session:
            assert session.scalar(select(TelegramSummaryMessageModel.status)) == "sent"
            assert session.scalar(select(TelegramDocumentMessageModel.status)) == (
                "running"
            )

    sender = _FakeSender(
        on_text=assert_text_claim_committed,
        on_document=assert_text_sent_before_document,
    )
    result = DeliveryStage(sender, lambda: UnitOfWork(session_factory)).execute(
        delivery_ids=(delivery_id,)
    )

    assert result.claimed_count == 2
    assert result.sent_count == 2
    assert result.failed_count == 0
    assert result.unknown_count == 0
    assert result.errors == ()
    assert sender.events == ["text", "document"]
    with Session(postgres_engine) as session:
        summary = session.scalar(select(TelegramSummaryMessageModel))
        document = session.scalar(select(TelegramDocumentMessageModel))
        assert summary is not None and summary.status == "sent"
        assert summary.telegram_message_id == 101
        assert summary.telegram_message_url.endswith("/101")
        assert document is not None and document.status == "sent"
        assert document.telegram_message_id == 202
        assert document.telegram_message_url.endswith("/202")


def test_document_failure_retries_only_document(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    content = b"%PDF-1.7\npartial\n%%EOF"
    _, _, delivery_id = _materialize_completed(
        session_factory,
        identity="partial-success",
        send_original_document=True,
        content=content,
    )
    first_sender = _FakeSender(
        document_errors=[TelegramSendFailed("document rejected")]
    )

    first = DeliveryStage(first_sender, lambda: UnitOfWork(session_factory)).execute(
        delivery_ids=(delivery_id,)
    )

    assert first_sender.events == ["text", "document"]
    assert first.sent_count == 1
    assert first.failed_count == 1
    retry_sender = _FakeSender()
    retry = DeliveryStage(retry_sender, lambda: UnitOfWork(session_factory)).execute(
        mode="failed", delivery_ids=(delivery_id,)
    )

    assert retry_sender.events == ["document"]
    assert retry.claimed_count == 1
    assert retry.sent_count == 1
    with Session(postgres_engine) as session:
        summary = session.scalar(select(TelegramSummaryMessageModel))
        document = session.scalar(select(TelegramDocumentMessageModel))
        assert summary is not None and summary.attempt_count == 1
        assert document is not None and document.attempt_count == 2
        assert document.status == "sent"


def test_failed_text_retry_does_not_expand_into_pending_document(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    content = b"%PDF-1.7\ntext-retry\n%%EOF"
    _, _, delivery_id = _materialize_completed(
        session_factory,
        identity="text-retry",
        send_original_document=True,
        content=content,
    )
    first_sender = _FakeSender(text_errors=[TelegramSendFailed("text rejected")])
    DeliveryStage(first_sender, lambda: UnitOfWork(session_factory)).execute(
        delivery_ids=(delivery_id,)
    )
    retry_sender = _FakeSender()
    stage = DeliveryStage(retry_sender, lambda: UnitOfWork(session_factory))

    retry = stage.execute(mode="failed", delivery_ids=(delivery_id,))

    assert retry_sender.events == ["text"]
    assert retry.claimed_count == 1
    with Session(postgres_engine) as session:
        document = session.scalar(select(TelegramDocumentMessageModel))
        assert document is not None and document.status == "pending"

    pending = stage.execute(mode="pending", delivery_ids=(delivery_id,))
    assert pending.claimed_count == 1
    assert retry_sender.events == ["text", "document"]


def test_confirmed_send_retries_only_idempotent_terminal_save_after_commit_error(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    content = b"%PDF-1.7\ncommit-uncertain\n%%EOF"
    _, _, delivery_id = _materialize_completed(
        session_factory,
        identity="commit-uncertain",
        send_original_document=False,
        content=content,
    )
    sender = _FakeSender()
    uow_factory = _CommitThenRaiseFactory(session_factory, fail_calls={2})

    result = DeliveryStage(sender, uow_factory).execute(delivery_ids=(delivery_id,))

    assert sender.events == ["text"]
    assert result.sent_count == 1
    assert result.errors == ()
    assert uow_factory.calls == 5
    with Session(postgres_engine) as session:
        message = session.scalar(select(TelegramSummaryMessageModel))
        assert message is not None and message.status == "sent"
        assert message.telegram_message_id == 101


def test_delivery_modes_reject_all_and_unknown(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    stage = DeliveryStage(_FakeSender(), lambda: UnitOfWork(session_factory))

    for invalid_mode in ("all", "unknown"):
        with pytest.raises(ValueError, match="pending 或 failed"):
            stage.execute(mode=invalid_mode)
        with pytest.raises(ValueError, match="pending 或 failed"):
            with UnitOfWork(session_factory) as uow:
                uow.telegram.claim_next_summary(mode=invalid_mode)


def test_unknown_is_not_reclaimed_by_pending_or_failed_modes(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    content = b"%PDF-1.7\nunknown\n%%EOF"
    _, _, delivery_id = _materialize_completed(
        session_factory,
        identity="unknown-result",
        send_original_document=True,
        content=content,
    )
    sender = _FakeSender(text_errors=[TelegramSendOutcomeUnknown("timeout")])
    stage = DeliveryStage(sender, lambda: UnitOfWork(session_factory))

    first = stage.execute(delivery_ids=(delivery_id,))
    failed_retry = stage.execute(mode="failed", delivery_ids=(delivery_id,))
    pending_retry = stage.execute(mode="pending", delivery_ids=(delivery_id,))

    assert first.unknown_count == 1
    assert failed_retry.claimed_count == 0
    assert pending_retry.claimed_count == 0
    assert sender.events == ["text"]
    with Session(postgres_engine) as session:
        summary = session.scalar(select(TelegramSummaryMessageModel))
        document = session.scalar(select(TelegramDocumentMessageModel))
        assert summary is not None and summary.status == "unknown"
        assert document is not None and document.status == "pending"


def test_stage_stale_recovery_marks_running_unknown(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    content = b"%PDF-1.7\nstale\n%%EOF"
    _, _, delivery_id = _materialize_completed(
        session_factory,
        identity="stale-message",
        send_original_document=False,
        content=content,
    )
    with UnitOfWork(session_factory) as uow:
        claim = uow.telegram.claim_next_summary(delivery_ids=(delivery_id,))
        assert claim is not None
    old_time = datetime.now(UTC) - timedelta(hours=2)
    with postgres_engine.begin() as connection:
        connection.execute(
            update(TelegramSummaryMessageModel)
            .where(TelegramSummaryMessageModel.telegram_delivery_id == delivery_id)
            .values(started_at=old_time)
        )

    stage = DeliveryStage(_FakeSender(), lambda: UnitOfWork(session_factory))
    recovered = stage.recover_stale(
        started_before=datetime.now(UTC) - timedelta(hours=1)
    )

    assert recovered.summary_messages == 1
    assert stage.execute(delivery_ids=(delivery_id,)).claimed_count == 0
    assert stage.execute(mode="failed", delivery_ids=(delivery_id,)).claimed_count == 0
