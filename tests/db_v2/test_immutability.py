"""首次决定、target、PDF 与 outbox payload 不可变测试。"""

import pytest
from sqlalchemy import Engine

from niuniu_stock_announcer.db.connection import create_session_factory
from niuniu_stock_announcer.db.errors import PersistenceConflictError
from niuniu_stock_announcer.db.schema import PdfSnapshot
from niuniu_stock_announcer.db.unit_of_work import UnitOfWork
from tests.db_v2.factories import (
    announcement,
    delivery,
    document_message,
    keyword_match,
    selected_match,
    summary_message,
)

pytestmark = pytest.mark.postgres


def test_duplicate_match_merges_keywords_without_overwriting_first_decision(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    with UnitOfWork(session_factory) as uow:
        record = uow.china_announcements.upsert(announcement())
        first = uow.china_matches.record(
            keyword_match(record.id, keywords=("回购", "进展"))
        )
    with UnitOfWork(session_factory) as uow:
        repeated = uow.china_matches.record(
            keyword_match(record.id, keywords=("进展", "方案"))
        )

    assert first.created is True
    assert repeated.created is False
    assert repeated.record.hit_count == 2
    assert repeated.record.matched_search_keywords == ("回购", "进展", "方案")
    assert repeated.record.filter_decisions == first.record.filter_decisions


def test_duplicate_match_rejects_changed_frozen_context(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    with UnitOfWork(session_factory) as uow:
        record = uow.china_announcements.upsert(announcement())
        uow.china_matches.record(selected_match(record.id, plan_key="frozen-decision"))

    with pytest.raises(PersistenceConflictError):
        with UnitOfWork(session_factory) as uow:
            uow.china_matches.record(
                selected_match(record.id, plan_key="frozen-decision", filtered=True)
            )


def test_pdf_snapshot_is_idempotent_but_cannot_be_overwritten(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    pdf = PdfSnapshot(
        storage_relative_path="cninfo/2026/08/a.pdf",
        size_bytes=1024,
        sha256="a" * 64,
    )
    with UnitOfWork(session_factory) as uow:
        record = uow.china_announcements.upsert(announcement())
        assert uow.china_announcements.attach_pdf(record.id, pdf).pdf == pdf
    with UnitOfWork(session_factory) as uow:
        assert uow.china_announcements.attach_pdf(record.id, pdf).pdf == pdf
    with pytest.raises(PersistenceConflictError):
        with UnitOfWork(session_factory) as uow:
            uow.china_announcements.attach_pdf(
                record.id,
                pdf.model_copy(update={"sha256": "b" * 64}),
            )


def test_delivery_target_and_document_intent_cannot_drift(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    with UnitOfWork(session_factory) as uow:
        record = uow.china_announcements.upsert(announcement())
        summary = uow.china_summaries.ensure(record.id)
        first = uow.telegram.ensure_delivery(delivery(summary.id))
    with UnitOfWork(session_factory) as uow:
        assert uow.telegram.ensure_delivery(delivery(summary.id)) == first
    with pytest.raises(PersistenceConflictError):
        with UnitOfWork(session_factory) as uow:
            uow.telegram.ensure_delivery(
                delivery(summary.id, target_url="https://t.me/changed/200")
            )
    with pytest.raises(PersistenceConflictError):
        with UnitOfWork(session_factory) as uow:
            uow.telegram.ensure_delivery(
                delivery(summary.id, send_original_document=False)
            )


def test_summary_and_document_payloads_are_insert_once(postgres_engine: Engine) -> None:
    session_factory = create_session_factory(postgres_engine)
    with UnitOfWork(session_factory) as uow:
        record = uow.china_announcements.upsert(announcement())
        summary = uow.china_summaries.ensure(record.id)
        delivery_record = uow.telegram.ensure_delivery(delivery(summary.id))
        text = uow.telegram.insert_summary_message(summary_message(delivery_record.id))
        document = uow.telegram.insert_document_message(
            document_message(delivery_record.id)
        )
    with UnitOfWork(session_factory) as uow:
        assert (
            uow.telegram.insert_summary_message(summary_message(delivery_record.id))
            == text
        )
        assert (
            uow.telegram.insert_document_message(document_message(delivery_record.id))
            == document
        )
    with pytest.raises(PersistenceConflictError):
        with UnitOfWork(session_factory) as uow:
            uow.telegram.insert_summary_message(
                summary_message(delivery_record.id, "重新渲染文本")
            )
    with pytest.raises(PersistenceConflictError):
        with UnitOfWork(session_factory) as uow:
            uow.telegram.insert_document_message(
                document_message(delivery_record.id, caption="改变 caption")
            )
