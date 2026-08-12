"""摘要与 Telegram 原子领取、恢复和状态转换测试。"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from niuniu_stock_announcer.db.connection import create_session_factory
from niuniu_stock_announcer.db.errors import InvalidStateTransitionError
from niuniu_stock_announcer.db.model import (
    ChinaSummaryModel,
    TelegramDocumentMessageModel,
)
from niuniu_stock_announcer.db.schema import PdfSnapshot
from niuniu_stock_announcer.db.unit_of_work import UnitOfWork
from tests.db_v2.factories import (
    announcement,
    delivery,
    document_message,
    summary_message,
)

pytestmark = pytest.mark.postgres


def _create_summary_queue(session_factory, count: int = 2) -> tuple[int, ...]:
    ids: list[int] = []
    with UnitOfWork(session_factory) as uow:
        for index in range(count):
            record = uow.china_announcements.upsert(
                announcement(f"summary-queue-{index}")
            )
            ids.append(uow.china_summaries.ensure(record.id).id)
    return tuple(ids)


def _create_telegram_queue(session_factory, count: int = 2) -> tuple[int, ...]:
    ids: list[int] = []
    with UnitOfWork(session_factory) as uow:
        for index in range(count):
            record = uow.china_announcements.upsert(
                announcement(f"telegram-queue-{index}")
            )
            summary = uow.china_summaries.ensure(record.id)
            parent = uow.telegram.ensure_delivery(
                delivery(summary.id, plan_key=f"telegram-plan-{index}")
            )
            uow.telegram.insert_summary_message(summary_message(parent.id))
            uow.telegram.insert_document_message(document_message(parent.id))
            ids.append(parent.id)
    return tuple(ids)


def test_summary_skip_locked_claims_distinct_rows(postgres_engine: Engine) -> None:
    session_factory = create_session_factory(postgres_engine)
    summary_ids = _create_summary_queue(session_factory)
    first_uow = UnitOfWork(session_factory)
    second_uow = UnitOfWork(session_factory)
    first_uow.__enter__()
    second_uow.__enter__()
    try:
        first = first_uow.china_summaries.claim_next()
        second = second_uow.china_summaries.claim_next()
        assert first is not None and second is not None
        assert {first.summary.id, second.summary.id} == set(summary_ids)
    finally:
        second_uow.__exit__(None, None, None)
        first_uow.__exit__(None, None, None)

    with UnitOfWork(session_factory) as uow:
        assert uow.china_summaries.claim_next() is None


def test_summary_stale_recovery_requires_explicit_retry(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    (summary_id,) = _create_summary_queue(session_factory, 1)
    with UnitOfWork(session_factory) as uow:
        claim = uow.china_summaries.claim_next()
        assert claim is not None
    old_time = datetime.now(UTC) - timedelta(hours=3)
    with postgres_engine.begin() as connection:
        connection.execute(
            update(ChinaSummaryModel)
            .where(ChinaSummaryModel.id == summary_id)
            .values(started_at=old_time)
        )
    with UnitOfWork(session_factory) as uow:
        assert (
            uow.china_summaries.recover_stale_running(
                started_before=datetime.now(UTC) - timedelta(hours=2)
            )
            == 1
        )
    with UnitOfWork(session_factory) as uow:
        assert uow.china_summaries.claim_next() is None
        retry = uow.china_summaries.claim_next(mode="failed")
        assert retry is not None
        assert retry.summary.id == summary_id
        assert retry.summary.failure_count == 1


def test_summary_skipped_requires_retry_exhaustion_and_verified_pdf(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    (summary_id,) = _create_summary_queue(session_factory, 1)
    with UnitOfWork(session_factory) as uow:
        claim = uow.china_summaries.claim_next()
        assert claim is not None
        uow.china_summaries.save_failed(
            summary_id, reason="first failure", failure_log="trace"
        )

    with pytest.raises(InvalidStateTransitionError):
        with UnitOfWork(session_factory) as uow:
            uow.china_summaries.lock(summary_id)
            uow.china_summaries.save_skipped(
                summary_id, reason="retry exhausted", minimum_failure_count=1
            )

    with UnitOfWork(session_factory) as uow:
        summary = uow.china_summaries.get(summary_id)
        assert summary is not None
        uow.china_announcements.attach_pdf(
            summary.china_announcement_id,
            PdfSnapshot(
                storage_relative_path="cninfo/2026/08/fallback.pdf",
                size_bytes=4096,
                sha256="a" * 64,
            ),
        )
    with pytest.raises(InvalidStateTransitionError):
        with UnitOfWork(session_factory) as uow:
            uow.china_summaries.lock(summary_id)
            uow.china_summaries.save_skipped(
                summary_id, reason="retry not exhausted", minimum_failure_count=2
            )
    with UnitOfWork(session_factory) as uow:
        uow.china_summaries.lock(summary_id)
        skipped = uow.china_summaries.save_skipped(
            summary_id, reason="retry exhausted", minimum_failure_count=1
        )
        assert skipped.status == "skipped"


def test_telegram_summary_claims_distinct_rows_and_document_waits_for_text(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    _create_telegram_queue(session_factory)
    first_uow = UnitOfWork(session_factory)
    second_uow = UnitOfWork(session_factory)
    first_uow.__enter__()
    second_uow.__enter__()
    try:
        first = first_uow.telegram.claim_next_summary()
        second = second_uow.telegram.claim_next_summary()
        assert first is not None and second is not None
        assert first.message.id != second.message.id
        assert first_uow.telegram.claim_next_document() is None
    finally:
        second_uow.__exit__(None, None, None)
        first_uow.__exit__(None, None, None)

    with UnitOfWork(session_factory) as uow:
        running = uow.telegram.claim_next_summary()
        assert running is None
    with UnitOfWork(session_factory) as uow:
        message = uow.telegram.save_summary_sent(
            first.message.id,
            result_chat_id=first.delivery.target_chat_id,
            telegram_message_id=101,
            result_message_thread_id=first.delivery.target_message_thread_id,
        )
        assert message.status == "sent"
    with UnitOfWork(session_factory) as uow:
        document = uow.telegram.claim_next_document()
        assert document is not None
        assert document.delivery.id == first.delivery.id


def test_telegram_failed_retry_and_unknown_are_disjoint(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    _create_telegram_queue(session_factory, 2)
    with UnitOfWork(session_factory) as uow:
        failed_claim = uow.telegram.claim_next_summary()
        assert failed_claim is not None
    with UnitOfWork(session_factory) as uow:
        uow.telegram.save_summary_failed(
            failed_claim.message.id, reason="bad request", failure_log="trace"
        )
    with UnitOfWork(session_factory) as uow:
        unknown_claim = uow.telegram.claim_next_summary()
        assert unknown_claim is not None
    with UnitOfWork(session_factory) as uow:
        uow.telegram.save_summary_unknown(
            unknown_claim.message.id, reason="timeout", failure_log="trace"
        )

    with UnitOfWork(session_factory) as uow:
        assert uow.telegram.claim_next_summary() is None
        retry = uow.telegram.claim_next_summary(mode="failed")
        assert retry is not None
        assert retry.message.id == failed_claim.message.id
    with UnitOfWork(session_factory) as uow:
        uow.telegram.save_summary_unknown(
            retry.message.id, reason="timeout again", failure_log="trace"
        )
    with UnitOfWork(session_factory) as uow:
        assert uow.telegram.claim_next_summary(mode="failed") is None


def test_stale_telegram_running_becomes_unknown_and_is_never_reclaimed(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    _create_telegram_queue(session_factory, 1)
    with UnitOfWork(session_factory) as uow:
        summary_claim = uow.telegram.claim_next_summary()
        assert summary_claim is not None
        uow.telegram.save_summary_sent(
            summary_claim.message.id,
            result_chat_id=summary_claim.delivery.target_chat_id,
            telegram_message_id=201,
        )
    with UnitOfWork(session_factory) as uow:
        document_claim = uow.telegram.claim_next_document()
        assert document_claim is not None
    old_time = datetime.now(UTC) - timedelta(hours=2)
    with postgres_engine.begin() as connection:
        connection.execute(
            update(TelegramDocumentMessageModel)
            .where(TelegramDocumentMessageModel.id == document_claim.message.id)
            .values(started_at=old_time)
        )
    with UnitOfWork(session_factory) as uow:
        recovered = uow.telegram.recover_stale_running(
            started_before=datetime.now(UTC) - timedelta(hours=1)
        )
        assert recovered.summary_messages == 0
        assert recovered.document_messages == 1
    with UnitOfWork(session_factory) as uow:
        assert uow.telegram.claim_next_document(mode="failed") is None
    with Session(postgres_engine) as session:
        status = session.scalar(
            select(TelegramDocumentMessageModel.status).where(
                TelegramDocumentMessageModel.id == document_claim.message.id
            )
        )
        assert status == "unknown"


def test_summary_failed_mode_does_not_claim_pending(postgres_engine: Engine) -> None:
    session_factory = create_session_factory(postgres_engine)
    failed_id, pending_id = _create_summary_queue(session_factory)
    with UnitOfWork(session_factory) as uow:
        claim = uow.china_summaries.claim_next()
        assert claim is not None and claim.summary.id == failed_id
    with UnitOfWork(session_factory) as uow:
        uow.china_summaries.save_failed(failed_id, reason="failed", failure_log="trace")
    with UnitOfWork(session_factory) as uow:
        retry = uow.china_summaries.claim_next(mode="failed")
        assert retry is not None and retry.summary.id == failed_id
    with UnitOfWork(session_factory) as uow:
        pending = uow.china_summaries.claim_next()
        assert pending is not None and pending.summary.id == pending_id


def test_telegram_failed_mode_does_not_claim_pending(postgres_engine: Engine) -> None:
    session_factory = create_session_factory(postgres_engine)
    _create_telegram_queue(session_factory, 2)
    with UnitOfWork(session_factory) as uow:
        failed_claim = uow.telegram.claim_next_summary()
        assert failed_claim is not None
    with UnitOfWork(session_factory) as uow:
        uow.telegram.save_summary_failed(
            failed_claim.message.id, reason="failed", failure_log="trace"
        )
    with UnitOfWork(session_factory) as uow:
        retry = uow.telegram.claim_next_summary(mode="failed")
        assert retry is not None and retry.message.id == failed_claim.message.id
    with UnitOfWork(session_factory) as uow:
        pending = uow.telegram.claim_next_summary()
        assert pending is not None
        assert pending.message.id != failed_claim.message.id


def test_document_results_preserve_payload_and_unknown_is_not_retried(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    _create_telegram_queue(session_factory, 3)
    for telegram_message_id in (301, 302, 303):
        with UnitOfWork(session_factory) as uow:
            claim = uow.telegram.claim_next_summary()
            assert claim is not None
        with UnitOfWork(session_factory) as uow:
            uow.telegram.save_summary_sent(
                claim.message.id,
                result_chat_id=claim.delivery.target_chat_id,
                telegram_message_id=telegram_message_id,
            )

    with UnitOfWork(session_factory) as uow:
        sent_claim = uow.telegram.claim_next_document()
        assert sent_claim is not None
    with UnitOfWork(session_factory) as uow:
        sent = uow.telegram.save_document_sent(
            sent_claim.message.id,
            result_chat_id=sent_claim.delivery.target_chat_id,
            telegram_message_id=401,
        )
    assert sent.storage_relative_path == sent_claim.message.storage_relative_path
    assert sent.document_sha256 == sent_claim.message.document_sha256

    with UnitOfWork(session_factory) as uow:
        failed_claim = uow.telegram.claim_next_document()
        assert failed_claim is not None
    with UnitOfWork(session_factory) as uow:
        failed = uow.telegram.save_document_failed(
            failed_claim.message.id, reason="bad document", failure_log="trace"
        )
    assert failed.storage_relative_path == failed_claim.message.storage_relative_path
    with UnitOfWork(session_factory) as uow:
        retry = uow.telegram.claim_next_document(mode="failed")
        assert retry is not None and retry.message.id == failed_claim.message.id
    with UnitOfWork(session_factory) as uow:
        unknown = uow.telegram.save_document_unknown(
            retry.message.id, reason="timeout", failure_log="trace"
        )
    assert unknown.document_sha256 == retry.message.document_sha256

    with UnitOfWork(session_factory) as uow:
        assert uow.telegram.claim_next_document(mode="failed") is None
        pending = uow.telegram.claim_next_document()
        assert pending is not None
