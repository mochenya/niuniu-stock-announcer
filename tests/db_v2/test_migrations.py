"""Alembic 与九表 schema 合同测试。"""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import Engine, inspect, text

from niuniu_stock_announcer.db.migration import (
    downgrade_database,
    get_current_revision,
    upgrade_database,
)
from niuniu_stock_announcer.db.model import Base

pytestmark = pytest.mark.postgres

BUSINESS_TABLES = {
    "cninfo_announcements",
    "sse_announcements",
    "szse_announcements",
    "china_announcements",
    "china_announcement_matches",
    "china_summaries",
    "telegram_deliveries",
    "telegram_summary_messages",
    "telegram_document_messages",
}

EXPECTED_COLUMNS = {
    "cninfo_announcements": (
        "id china_announcement_id announcement_id sec_code sec_name org_id "
        "announcement_title announcement_time_ms adjunct_url adjunct_size "
        "adjunct_type column_id page_column announcement_type first_seen_at "
        "last_seen_at"
    ).split(),
    "sse_announcements": (
        "id china_announcement_id provider_announcement_id security_code "
        "security_name org_bulletin_id title sse_date url bulletin_type_desc "
        "is_holder_disclose first_seen_at last_seen_at"
    ).split(),
    "szse_announcements": (
        "id china_announcement_id provider_announcement_id ann_id source_record_id "
        "sec_codes sec_names title publish_time attach_path attach_format attach_size "
        "bond_type big_industry_code big_category_id small_category_id channel_code "
        "first_seen_at last_seen_at"
    ).split(),
    "china_announcements": (
        "id provider_key provider_announcement_id market_scope exchanges stock_codes "
        "stock_names title published_at source_url pdf_storage_relative_path "
        "pdf_size_bytes pdf_sha256 first_seen_at last_seen_at"
    ).split(),
    "china_announcement_matches": (
        "id china_announcement_id plan_key discovery_type market_scope query_exchange "
        "query_stock_code query_provider_key matched_search_keywords filter_status "
        "filter_decisions first_seen_at last_seen_at hit_count"
    ).split(),
    "china_summaries": (
        "id china_announcement_id status failure_count agent_key agent_version "
        "prompt_version model_provider model_name input_tokens output_tokens "
        "summary_text summary_tags summary_result failure_reason failure_log "
        "started_at finished_at created_at updated_at"
    ).split(),
    "telegram_deliveries": (
        "id producer_key business_key plan_key market_scope target_key target_url "
        "target_chat_id target_message_thread_id send_original_document created_at"
    ).split(),
    "telegram_summary_messages": (
        "id telegram_delivery_id text_content status attempt_count result_chat_id "
        "result_message_thread_id telegram_message_id telegram_message_url "
        "failure_reason failure_log started_at sent_at created_at updated_at"
    ).split(),
    "telegram_document_messages": (
        "id telegram_delivery_id document_key source_url storage_relative_path "
        "document_filename document_mime_type document_size_bytes document_sha256 "
        "document_caption status attempt_count result_chat_id "
        "result_message_thread_id telegram_message_id telegram_message_url "
        "failure_reason failure_log started_at sent_at created_at updated_at"
    ).split(),
}

EXPECTED_FOREIGN_KEYS = {
    "cninfo_announcements": ("china_announcement_id", "china_announcements"),
    "sse_announcements": ("china_announcement_id", "china_announcements"),
    "szse_announcements": ("china_announcement_id", "china_announcements"),
    "china_announcement_matches": (
        "china_announcement_id",
        "china_announcements",
    ),
    "china_summaries": ("china_announcement_id", "china_announcements"),
    "telegram_summary_messages": (
        "telegram_delivery_id",
        "telegram_deliveries",
    ),
    "telegram_document_messages": (
        "telegram_delivery_id",
        "telegram_deliveries",
    ),
}

EXPECTED_NULLABLE_COLUMNS = {
    "cninfo_announcements": {
        "sec_code",
        "sec_name",
        "org_id",
        "announcement_title",
        "announcement_time_ms",
        "adjunct_url",
        "adjunct_size",
        "adjunct_type",
        "column_id",
        "page_column",
        "announcement_type",
    },
    "sse_announcements": {
        "security_code",
        "security_name",
        "org_bulletin_id",
        "title",
        "sse_date",
        "url",
        "bulletin_type_desc",
        "is_holder_disclose",
    },
    "szse_announcements": {
        "ann_id",
        "source_record_id",
        "title",
        "publish_time",
        "attach_path",
        "attach_format",
        "attach_size",
        "bond_type",
        "big_industry_code",
        "big_category_id",
        "small_category_id",
        "channel_code",
    },
    "china_announcements": {
        "pdf_storage_relative_path",
        "pdf_size_bytes",
        "pdf_sha256",
    },
    "china_announcement_matches": {
        "query_exchange",
        "query_stock_code",
        "query_provider_key",
    },
    "china_summaries": {
        "agent_key",
        "agent_version",
        "prompt_version",
        "model_provider",
        "model_name",
        "input_tokens",
        "output_tokens",
        "summary_text",
        "summary_tags",
        "summary_result",
        "failure_reason",
        "failure_log",
        "started_at",
        "finished_at",
    },
    "telegram_deliveries": {"target_message_thread_id"},
    "telegram_summary_messages": {
        "result_chat_id",
        "result_message_thread_id",
        "telegram_message_id",
        "telegram_message_url",
        "failure_reason",
        "failure_log",
        "started_at",
        "sent_at",
    },
    "telegram_document_messages": {
        "result_chat_id",
        "result_message_thread_id",
        "telegram_message_id",
        "telegram_message_url",
        "failure_reason",
        "failure_log",
        "started_at",
        "sent_at",
    },
}

EXPECTED_TYPES = {
    "cninfo_announcements": (
        "BIGINT BIGINT TEXT TEXT TEXT TEXT TEXT BIGINT TEXT BIGINT TEXT TEXT TEXT "
        "TEXT TIMESTAMP TIMESTAMP"
    ).split(),
    "sse_announcements": (
        "BIGINT BIGINT TEXT TEXT TEXT TEXT TEXT TEXT TEXT TEXT TEXT TIMESTAMP TIMESTAMP"
    ).split(),
    "szse_announcements": (
        "BIGINT BIGINT TEXT TEXT TEXT ARRAY ARRAY TEXT TEXT TEXT TEXT BIGINT TEXT TEXT "
        "TEXT TEXT TEXT TIMESTAMP TIMESTAMP"
    ).split(),
    "china_announcements": (
        "BIGINT TEXT TEXT TEXT ARRAY ARRAY ARRAY TEXT TIMESTAMP TEXT TEXT BIGINT TEXT "
        "TIMESTAMP TIMESTAMP"
    ).split(),
    "china_announcement_matches": (
        "BIGINT BIGINT TEXT TEXT TEXT TEXT TEXT TEXT ARRAY TEXT JSONB TIMESTAMP "
        "TIMESTAMP BIGINT"
    ).split(),
    "china_summaries": (
        "BIGINT BIGINT TEXT INTEGER TEXT TEXT TEXT TEXT TEXT BIGINT BIGINT TEXT ARRAY "
        "JSONB TEXT TEXT TIMESTAMP TIMESTAMP TIMESTAMP TIMESTAMP"
    ).split(),
    "telegram_deliveries": (
        "BIGINT TEXT TEXT TEXT TEXT TEXT TEXT BIGINT BIGINT BOOLEAN TIMESTAMP"
    ).split(),
    "telegram_summary_messages": (
        "BIGINT BIGINT TEXT TEXT INTEGER BIGINT BIGINT BIGINT TEXT TEXT TEXT TIMESTAMP "
        "TIMESTAMP TIMESTAMP TIMESTAMP"
    ).split(),
    "telegram_document_messages": (
        "BIGINT BIGINT TEXT TEXT TEXT TEXT TEXT BIGINT TEXT TEXT TEXT INTEGER BIGINT "
        "BIGINT BIGINT TEXT TEXT TEXT TIMESTAMP TIMESTAMP TIMESTAMP TIMESTAMP"
    ).split(),
}

EXPECTED_UNIQUES = {
    "cninfo_announcements": {
        "uq_cninfo_announcements_china_announcement",
        "uq_cninfo_announcements_announcement_id",
    },
    "sse_announcements": {
        "uq_sse_announcements_china_announcement",
        "uq_sse_announcements_provider_announcement_id",
    },
    "szse_announcements": {
        "uq_szse_announcements_china_announcement",
        "uq_szse_announcements_provider_announcement_id",
    },
    "china_announcements": {"uq_china_announcements_provider_identity"},
    "china_announcement_matches": {"uq_china_announcement_matches_announcement_plan"},
    "china_summaries": {"uq_china_summaries_china_announcement"},
    "telegram_deliveries": {"uq_telegram_deliveries_business_identity"},
    "telegram_summary_messages": {"uq_telegram_summary_messages_delivery"},
    "telegram_document_messages": {"uq_telegram_document_messages_delivery_document"},
}

EXPECTED_CHECKS = {
    "cninfo_announcements": {"ck_cninfo_announcements_adjunct_size"},
    "sse_announcements": set(),
    "szse_announcements": {
        "ck_szse_announcements_security_arrays",
        "ck_szse_announcements_attach_size",
    },
    "china_announcements": {
        "ck_china_announcements_provider_key",
        "ck_china_announcements_market_scope",
        "ck_china_announcements_security_array_alignment",
        "ck_china_announcements_security_array_values",
        "ck_china_announcements_scope_exchanges",
        "ck_china_announcements_pdf_snapshot",
    },
    "china_announcement_matches": {
        "ck_china_announcement_matches_plan_key",
        "ck_china_announcement_matches_discovery_type",
        "ck_china_announcement_matches_market_scope",
        "ck_china_announcement_matches_filter_status",
        "ck_china_announcement_matches_hit_count",
        "ck_china_announcement_matches_filter_decisions",
        "ck_china_announcement_matches_filter_projection",
        "ck_china_announcement_matches_keyword_values",
        "ck_china_announcement_matches_discovery_shape",
        "ck_china_announcement_matches_provider_route",
    },
    "china_summaries": {
        "ck_china_summaries_status",
        "ck_china_summaries_failure_count",
        "ck_china_summaries_token_counts",
        "ck_china_summaries_result_object",
        "ck_china_summaries_result_projection",
        "ck_china_summaries_success_fields",
        "ck_china_summaries_timing",
    },
    "telegram_deliveries": set(),
    "telegram_summary_messages": {
        "ck_telegram_summary_messages_status",
        "ck_telegram_summary_messages_attempt_count",
        "ck_telegram_summary_messages_result_fields",
        "ck_telegram_summary_messages_lifecycle",
    },
    "telegram_document_messages": {
        "ck_telegram_document_messages_document_size",
        "ck_telegram_document_messages_document_sha256",
        "ck_telegram_document_messages_status",
        "ck_telegram_document_messages_attempt_count",
        "ck_telegram_document_messages_result_fields",
        "ck_telegram_document_messages_lifecycle",
    },
}


def test_migration_round_trip_matches_declarative_metadata(
    empty_postgres_engine: Engine,
) -> None:
    assert get_current_revision(empty_postgres_engine) is None

    upgrade_database(empty_postgres_engine)
    assert get_current_revision(empty_postgres_engine) == "20260812_0001"
    assert set(inspect(empty_postgres_engine).get_table_names()) == BUSINESS_TABLES | {
        "alembic_version"
    }
    with empty_postgres_engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "compare_server_default": True},
        )
        assert compare_metadata(context, Base.metadata) == []

    downgrade_database(empty_postgres_engine)
    assert get_current_revision(empty_postgres_engine) is None
    assert inspect(empty_postgres_engine).get_table_names() == ["alembic_version"]

    upgrade_database(empty_postgres_engine)
    assert get_current_revision(empty_postgres_engine) == "20260812_0001"
    assert set(inspect(empty_postgres_engine).get_table_names()) == BUSINESS_TABLES | {
        "alembic_version"
    }


def test_schema_catalog_matches_frozen_contract(postgres_engine: Engine) -> None:
    inspector = inspect(postgres_engine)
    for table_name, expected_columns in EXPECTED_COLUMNS.items():
        columns = inspector.get_columns(table_name)
        assert [column["name"] for column in columns] == expected_columns
        assert [type(column["type"]).__name__ for column in columns] == EXPECTED_TYPES[
            table_name
        ]
        for column in columns:
            if type(column["type"]).__name__ == "TIMESTAMP":
                assert column["type"].timezone is True
            if type(column["type"]).__name__ == "ARRAY":
                assert type(column["type"].item_type).__name__ == "TEXT"
        assert {
            column["name"] for column in columns if column["nullable"]
        } == EXPECTED_NULLABLE_COLUMNS[table_name]
        id_column = columns[0]
        assert id_column["name"] == "id"
        assert id_column["nullable"] is False
        assert id_column["identity"]["always"] is False

    for table_name, (column_name, referred_table) in EXPECTED_FOREIGN_KEYS.items():
        foreign_keys = inspector.get_foreign_keys(table_name)
        assert len(foreign_keys) == 1
        foreign_key = foreign_keys[0]
        assert foreign_key["constrained_columns"] == [column_name]
        assert foreign_key["referred_table"] == referred_table
        assert foreign_key["options"] == {"ondelete": "RESTRICT"}

    for table_name in BUSINESS_TABLES:
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(table_name)
        } == EXPECTED_UNIQUES[table_name]
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints(table_name)
        } == EXPECTED_CHECKS[table_name]


def test_initial_indexes_cover_declared_query_paths(postgres_engine: Engine) -> None:
    inspector = inspect(postgres_engine)
    index_names = {
        index["name"]
        for table_name in BUSINESS_TABLES
        for index in inspector.get_indexes(table_name)
        if not index["unique"]
    }
    assert index_names == {
        "idx_china_announcements_published_at",
        "idx_china_announcements_stock_codes_gin",
        "idx_china_announcement_matches_plan_status_first_seen",
        "idx_china_summaries_claim",
        "idx_telegram_deliveries_plan_scope_created",
        "idx_telegram_summary_messages_claim",
        "idx_telegram_document_messages_claim",
    }
    with postgres_engine.connect() as connection:
        definitions = dict(
            connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND indexname LIKE 'idx_%'"
                )
            ).all()
        )
    assert "published_at DESC" in definitions["idx_china_announcements_published_at"]
    assert (
        "first_seen_at DESC"
        in definitions["idx_china_announcement_matches_plan_status_first_seen"]
    )
    assert (
        "USING gin (stock_codes)"
        in definitions["idx_china_announcements_stock_codes_gin"]
    )
