"""直接击穿 Pydantic 后验证 PostgreSQL 业务约束。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, insert, update
from sqlalchemy.exc import IntegrityError

from niuniu_stock_announcer.db.connection import create_session_factory
from niuniu_stock_announcer.db.model import (
    ChinaAnnouncementMatchModel,
    ChinaAnnouncementModel,
    ChinaSummaryModel,
    TelegramDocumentMessageModel,
    TelegramSummaryMessageModel,
)
from niuniu_stock_announcer.db.unit_of_work import UnitOfWork
from tests.db_v2.factories import announcement, delivery

pytestmark = pytest.mark.postgres


def _announcement_values(
    identity: str = "constraint-announcement",
) -> dict[str, object]:
    return {
        "provider_key": "cninfo",
        "provider_announcement_id": identity,
        "market_scope": "a_share",
        "exchanges": ["sh"],
        "stock_codes": ["688090"],
        "stock_names": ["瑞松科技"],
        "title": "约束测试公告",
        "published_at": datetime(2026, 8, 12, 9, tzinfo=UTC),
        "source_url": f"https://example.invalid/{identity}.pdf",
    }


@pytest.mark.parametrize(
    "override",
    [
        {"stock_codes": []},
        {"exchanges": ["hk"]},
        {"stock_codes": [""]},
        {"pdf_storage_relative_path": "a.pdf", "pdf_size_bytes": 1},
        {
            "pdf_storage_relative_path": "a.pdf",
            "pdf_size_bytes": 0,
            "pdf_sha256": "A" * 64,
        },
    ],
)
def test_china_announcement_checks_reject_invalid_projection(
    postgres_engine: Engine, override: dict[str, object]
) -> None:
    values = _announcement_values(str(abs(hash(repr(override))))) | override
    with pytest.raises(IntegrityError):
        with postgres_engine.begin() as connection:
            connection.execute(insert(ChinaAnnouncementModel).values(**values))


@pytest.mark.parametrize(
    "override",
    [
        {"filter_decisions": {}},
        {"filter_decisions": []},
        {"filter_decisions": [{"outcome": None}]},
        {"filter_decisions": [{"outcome": "unknown"}]},
        {
            "filter_decisions": [{"outcome": "filtered"}],
            "filter_status": "selected",
        },
        {"matched_search_keywords": ["回购"]},
        {"query_provider_key": "szse"},
        {"hit_count": 0},
    ],
)
def test_match_checks_reject_invalid_discovery_or_filter_projection(
    postgres_engine: Engine, override: dict[str, object]
) -> None:
    with postgres_engine.begin() as connection:
        announcement_id = connection.execute(
            insert(ChinaAnnouncementModel)
            .values(**_announcement_values(str(abs(hash(repr(override))))))
            .returning(ChinaAnnouncementModel.id)
        ).scalar_one()
    values = {
        "china_announcement_id": announcement_id,
        "plan_key": "constraint-plan",
        "discovery_type": "selected_stocks",
        "market_scope": "a_share",
        "query_exchange": "sh",
        "query_stock_code": "688090",
        "query_provider_key": "cninfo",
        "matched_search_keywords": [],
        "filter_status": "selected",
        "filter_decisions": [{"outcome": "selected"}],
    } | override
    with pytest.raises(IntegrityError):
        with postgres_engine.begin() as connection:
            connection.execute(insert(ChinaAnnouncementMatchModel).values(**values))


@pytest.mark.parametrize(
    "values",
    [
        {"status": "running"},
        {
            "status": "completed",
            "agent_key": "china",
            "agent_version": "v1",
            "prompt_version": "v1",
            "model_name": "model",
            "summary_text": "文本 A",
            "summary_tags": ["回购"],
            "summary_result": {
                "schema_version": "china-announcement-summary.v1",
                "summary_text": "文本 B",
                "summary_tags": ["回购"],
            },
            "finished_at": datetime.now(UTC),
        },
        {
            "status": "completed",
            "agent_key": "china",
            "agent_version": "v1",
            "prompt_version": "v1",
            "model_name": "model",
            "summary_text": "文本",
            "summary_tags": ["回购"],
            "summary_result": {
                "schema_version": "unexpected.v1",
                "summary_text": "文本",
                "summary_tags": ["回购"],
            },
            "finished_at": datetime.now(UTC),
        },
        {
            "status": "completed",
            "agent_key": "china",
            "agent_version": "v1",
            "prompt_version": "v1",
            "model_name": "model",
            "summary_text": "文本",
            "summary_tags": ["回购"],
            "summary_result": {"schema_version": "china-announcement-summary.v1"},
            "finished_at": datetime.now(UTC),
        },
        {
            "status": "failed",
            "failure_reason": "failed",
            "finished_at": datetime.now(UTC),
            "summary_text": "残留摘要",
        },
        {"status": "skipped", "finished_at": datetime.now(UTC)},
    ],
)
def test_summary_checks_reject_invalid_state_projection(
    postgres_engine: Engine, values: dict[str, object]
) -> None:
    session_factory = create_session_factory(postgres_engine)
    with UnitOfWork(session_factory) as uow:
        record = uow.china_announcements.upsert(announcement("summary-constraint"))
    with pytest.raises(IntegrityError):
        with postgres_engine.begin() as connection:
            connection.execute(
                insert(ChinaSummaryModel).values(
                    china_announcement_id=record.id, **values
                )
            )


@pytest.mark.parametrize(
    "model_type, values",
    [
        (
            TelegramSummaryMessageModel,
            {"text_content": "文本", "status": "sent"},
        ),
        (
            TelegramSummaryMessageModel,
            {
                "text_content": "文本",
                "status": "pending",
                "telegram_message_id": 10,
            },
        ),
        (
            TelegramDocumentMessageModel,
            {
                "document_key": "original",
                "source_url": "https://example.invalid/a.pdf",
                "storage_relative_path": "a.pdf",
                "document_filename": "a.pdf",
                "document_mime_type": "application/pdf",
                "document_size_bytes": 0,
                "document_sha256": "a" * 64,
                "document_caption": "公告",
            },
        ),
        (
            TelegramDocumentMessageModel,
            {
                "document_key": "original",
                "source_url": "https://example.invalid/a.pdf",
                "storage_relative_path": "a.pdf",
                "document_filename": "a.pdf",
                "document_mime_type": "application/pdf",
                "document_size_bytes": 1,
                "document_sha256": "A" * 64,
                "document_caption": "公告",
            },
        ),
    ],
)
def test_telegram_message_checks_reject_invalid_payload_or_state(
    postgres_engine: Engine, model_type, values: dict[str, object]
) -> None:
    session_factory = create_session_factory(postgres_engine)
    with UnitOfWork(session_factory) as uow:
        record = uow.china_announcements.upsert(announcement("telegram-constraint"))
        summary = uow.china_summaries.ensure(record.id)
        delivery_record = uow.telegram.ensure_delivery(delivery(summary.id))
    with pytest.raises(IntegrityError):
        with postgres_engine.begin() as connection:
            connection.execute(
                insert(model_type).values(
                    telegram_delivery_id=delivery_record.id, **values
                )
            )


def test_foreign_keys_and_unique_business_identity_are_enforced(
    postgres_engine: Engine,
) -> None:
    with pytest.raises(IntegrityError):
        with postgres_engine.begin() as connection:
            connection.execute(
                insert(ChinaSummaryModel).values(china_announcement_id=999999)
            )

    values = _announcement_values("duplicate-identity")
    with postgres_engine.begin() as connection:
        connection.execute(insert(ChinaAnnouncementModel).values(**values))
    with pytest.raises(IntegrityError):
        with postgres_engine.begin() as connection:
            connection.execute(insert(ChinaAnnouncementModel).values(**values))


def test_database_rejects_success_projection_mutation(postgres_engine: Engine) -> None:
    session_factory = create_session_factory(postgres_engine)
    with UnitOfWork(session_factory) as uow:
        record = uow.china_announcements.upsert(announcement("projection-update"))
        summary = uow.china_summaries.ensure(record.id)
        claim = uow.china_summaries.claim_next()
        assert claim is not None and claim.summary.id == summary.id
    with pytest.raises(IntegrityError):
        with postgres_engine.begin() as connection:
            connection.execute(
                update(ChinaSummaryModel)
                .where(ChinaSummaryModel.id == summary.id)
                .values(
                    status="completed",
                    agent_key="china",
                    agent_version="v1",
                    prompt_version="v1",
                    model_name="model",
                    summary_text="文本",
                    summary_tags=["回购"],
                    summary_result={"summary_text": "文本", "summary_tags": []},
                    finished_at=datetime.now(UTC),
                )
            )
