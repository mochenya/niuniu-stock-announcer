"""summary 统一行锁与 Telegram child 原子物化竞态测试。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from niuniu_stock_announcer.db.connection import create_session_factory
from niuniu_stock_announcer.db.errors import InvalidStateTransitionError
from niuniu_stock_announcer.db.model import (
    ChinaSummaryModel,
    TelegramDeliveryModel,
    TelegramDocumentMessageModel,
    TelegramSummaryMessageModel,
)
from niuniu_stock_announcer.db.schema import TelegramDeliveryRecord
from niuniu_stock_announcer.db.unit_of_work import UnitOfWork
from tests.db_v2.factories import (
    announcement,
    delivery,
    document_message,
    selected_match,
    summary_completion,
    summary_message,
)

pytestmark = pytest.mark.postgres


def _materialize_all_messages(uow: UnitOfWork, summary_id: int) -> None:
    for parent in uow.telegram.list_deliveries(
        producer_key="china_summary", business_key=str(summary_id)
    ):
        uow.telegram.insert_summary_message(
            summary_message(parent.id, f"summary={summary_id};plan={parent.plan_key}")
        )
        if parent.send_original_document:
            uow.telegram.insert_document_message(document_message(parent.id))


def _create_running_summary(session_factory) -> tuple[int, int]:
    with UnitOfWork(session_factory) as uow:
        record = uow.china_announcements.upsert(announcement("materialization"))
        uow.china_matches.record(selected_match(record.id, plan_key="plan-alpha"))
        summary = uow.china_summaries.ensure(record.id)
        uow.china_summaries.lock(summary.id)
        parent = uow.telegram.ensure_delivery(
            delivery(summary.id, plan_key="plan-alpha")
        )
    with UnitOfWork(session_factory) as uow:
        claim = uow.china_summaries.claim_next()
        assert claim is not None and claim.summary.id == summary.id
    return summary.id, parent.id


def _insert_delivery_and_materialize_if_terminal(
    session_factory,
    *,
    summary_id: int,
    plan_key: str,
) -> TelegramDeliveryRecord:
    with UnitOfWork(session_factory) as uow:
        locked = uow.china_summaries.lock(summary_id)
        parent = uow.telegram.ensure_delivery(delivery(summary_id, plan_key=plan_key))
        if locked.status in {"completed", "skipped"}:
            uow.telegram.insert_summary_message(
                summary_message(
                    parent.id, f"summary={summary_id};plan={parent.plan_key}"
                )
            )
            if parent.send_original_document:
                uow.telegram.insert_document_message(document_message(parent.id))
        return parent


def test_terminal_summary_and_child_materialization_roll_back_together(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    summary_id, _ = _create_running_summary(session_factory)

    with pytest.raises(RuntimeError, match="materialization failed"):
        with UnitOfWork(session_factory) as uow:
            uow.china_summaries.lock(summary_id)
            uow.china_summaries.save_completed(summary_id, summary_completion())
            _materialize_all_messages(uow, summary_id)
            raise RuntimeError("materialization failed")

    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(ChinaSummaryModel.status).where(
                    ChinaSummaryModel.id == summary_id
                )
            )
            == "running"
        )
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramSummaryMessageModel)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramDocumentMessageModel)
            )
            == 0
        )

    with UnitOfWork(session_factory) as uow:
        uow.china_summaries.lock(summary_id)
        completed = uow.china_summaries.save_completed(summary_id, summary_completion())
        _materialize_all_messages(uow, summary_id)
        assert completed.status == "completed"
    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramSummaryMessageModel)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramDocumentMessageModel)
            )
            == 1
        )


def test_terminal_save_requires_explicit_summary_lock(postgres_engine: Engine) -> None:
    session_factory = create_session_factory(postgres_engine)
    summary_id, _ = _create_running_summary(session_factory)
    with pytest.raises(InvalidStateTransitionError, match="必须先锁定"):
        with UnitOfWork(session_factory) as uow:
            uow.china_summaries.save_completed(summary_id, summary_completion())


def test_new_plan_after_completed_summary_reuses_result_and_materializes_own_child(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    summary_id, _ = _create_running_summary(session_factory)
    with UnitOfWork(session_factory) as uow:
        uow.china_summaries.lock(summary_id)
        uow.china_summaries.save_completed(summary_id, summary_completion())
        _materialize_all_messages(uow, summary_id)

    parent = _insert_delivery_and_materialize_if_terminal(
        session_factory, summary_id=summary_id, plan_key="plan-beta"
    )

    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(ChinaSummaryModel)) == 1
        assert (
            session.scalar(select(func.count()).select_from(TelegramDeliveryModel)) == 2
        )
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramSummaryMessageModel)
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramDocumentMessageModel)
            )
            == 2
        )
        assert (
            session.scalar(
                select(TelegramSummaryMessageModel.text_content).where(
                    TelegramSummaryMessageModel.telegram_delivery_id == parent.id
                )
            )
            == f"summary={summary_id};plan=plan-beta"
        )


def test_delivery_created_while_summary_running_is_seen_by_terminal_transaction(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    summary_id, _ = _create_running_summary(session_factory)
    parent = _insert_delivery_and_materialize_if_terminal(
        session_factory, summary_id=summary_id, plan_key="plan-beta"
    )
    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(TelegramSummaryMessageModel.id).where(
                    TelegramSummaryMessageModel.telegram_delivery_id == parent.id
                )
            )
            is None
        )

    with UnitOfWork(session_factory) as uow:
        uow.china_summaries.lock(summary_id)
        uow.china_summaries.save_completed(summary_id, summary_completion())
        _materialize_all_messages(uow, summary_id)

    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramSummaryMessageModel)
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramDocumentMessageModel)
            )
            == 2
        )


def test_terminal_lock_wins_race_and_later_delivery_observes_completed_state(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    summary_id, _ = _create_running_summary(session_factory)
    terminal_has_lock = Event()
    allow_terminal_commit = Event()
    delivery_started = Event()

    def terminal_transaction() -> None:
        with UnitOfWork(session_factory) as uow:
            uow.china_summaries.lock(summary_id)
            terminal_has_lock.set()
            assert allow_terminal_commit.wait(timeout=5)
            uow.china_summaries.save_completed(summary_id, summary_completion())
            _materialize_all_messages(uow, summary_id)

    def delivery_transaction() -> TelegramDeliveryRecord:
        assert terminal_has_lock.wait(timeout=5)
        delivery_started.set()
        return _insert_delivery_and_materialize_if_terminal(
            session_factory, summary_id=summary_id, plan_key="plan-racing"
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        terminal_future = executor.submit(terminal_transaction)
        delivery_future = executor.submit(delivery_transaction)
        assert delivery_started.wait(timeout=5)
        allow_terminal_commit.set()
        terminal_future.result(timeout=5)
        parent = delivery_future.result(timeout=5)

    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(TelegramSummaryMessageModel.id).where(
                    TelegramSummaryMessageModel.telegram_delivery_id == parent.id
                )
            )
            is not None
        )
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramSummaryMessageModel)
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramDocumentMessageModel)
            )
            == 2
        )


def test_delivery_lock_wins_race_and_terminal_sees_new_parent(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    summary_id, _ = _create_running_summary(session_factory)
    delivery_has_lock = Event()
    allow_delivery_commit = Event()
    terminal_started = Event()

    def delivery_transaction() -> TelegramDeliveryRecord:
        with UnitOfWork(session_factory) as uow:
            locked = uow.china_summaries.lock(summary_id)
            assert locked.status == "running"
            parent = uow.telegram.ensure_delivery(
                delivery(summary_id, plan_key="plan-racing")
            )
            delivery_has_lock.set()
            assert allow_delivery_commit.wait(timeout=5)
            return parent

    def terminal_transaction() -> None:
        assert delivery_has_lock.wait(timeout=5)
        terminal_started.set()
        with UnitOfWork(session_factory) as uow:
            uow.china_summaries.lock(summary_id)
            uow.china_summaries.save_completed(summary_id, summary_completion())
            _materialize_all_messages(uow, summary_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        delivery_future = executor.submit(delivery_transaction)
        terminal_future = executor.submit(terminal_transaction)
        assert terminal_started.wait(timeout=5)
        allow_delivery_commit.set()
        parent = delivery_future.result(timeout=5)
        terminal_future.result(timeout=5)

    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(TelegramSummaryMessageModel.id).where(
                    TelegramSummaryMessageModel.telegram_delivery_id == parent.id
                )
            )
            is not None
        )
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramSummaryMessageModel)
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramDocumentMessageModel)
            )
            == 2
        )
