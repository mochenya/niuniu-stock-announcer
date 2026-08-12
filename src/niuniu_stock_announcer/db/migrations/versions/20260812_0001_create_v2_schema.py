"""创建 v2 schema

Revision ID: 20260812_0001
Revises:
Create Date: 2026-08-12 21:58:16.458144
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """升级 schema。"""
    # 三个证券数组共同表达按位置配对的证券；错位会把 exchange 与代码映射到不同主体。
    op.create_table(
        "china_announcements",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("provider_key", sa.Text(), nullable=False),
        sa.Column("provider_announcement_id", sa.Text(), nullable=False),
        sa.Column("market_scope", sa.Text(), nullable=False),
        sa.Column("exchanges", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("stock_codes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("stock_names", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("pdf_storage_relative_path", sa.Text(), nullable=True),
        sa.Column("pdf_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("pdf_sha256", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(pdf_storage_relative_path IS NULL AND pdf_size_bytes IS NULL AND pdf_sha256 IS NULL) OR (pdf_storage_relative_path IS NOT NULL AND pdf_size_bytes IS NOT NULL AND pdf_size_bytes > 0 AND pdf_sha256 IS NOT NULL AND pdf_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_china_announcements_pdf_snapshot",
        ),
        sa.CheckConstraint(
            "array_position(exchanges, NULL) IS NULL AND array_position(exchanges, '') IS NULL AND array_position(stock_codes, NULL) IS NULL AND array_position(stock_codes, '') IS NULL AND array_position(stock_names, '') IS NULL",
            name="ck_china_announcements_security_array_values",
        ),
        sa.CheckConstraint(
            "cardinality(exchanges) = 0 OR (market_scope = 'a_share' AND exchanges <@ ARRAY['sh', 'sz', 'bj']::text[]) OR (market_scope = 'hk' AND exchanges <@ ARRAY['hk']::text[])",
            name="ck_china_announcements_scope_exchanges",
        ),
        sa.CheckConstraint(
            "market_scope IN ('a_share', 'hk')",
            name="ck_china_announcements_market_scope",
        ),
        sa.CheckConstraint(
            "provider_key IN ('cninfo', 'sse', 'szse')",
            name="ck_china_announcements_provider_key",
        ),
        sa.CheckConstraint(
            "cardinality(exchanges) = cardinality(stock_codes) AND cardinality(exchanges) = cardinality(stock_names)",
            name="ck_china_announcements_security_array_alignment",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_key",
            "provider_announcement_id",
            name="uq_china_announcements_provider_identity",
        ),
    )
    op.create_index(
        "idx_china_announcements_published_at",
        "china_announcements",
        [sa.literal_column("published_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_china_announcements_stock_codes_gin",
        "china_announcements",
        ["stock_codes"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "telegram_deliveries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("producer_key", sa.Text(), nullable=False),
        sa.Column("business_key", sa.Text(), nullable=False),
        sa.Column("plan_key", sa.Text(), nullable=False),
        sa.Column("market_scope", sa.Text(), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("target_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("target_message_thread_id", sa.BigInteger(), nullable=True),
        sa.Column("send_original_document", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "producer_key",
            "business_key",
            "plan_key",
            "market_scope",
            name="uq_telegram_deliveries_business_identity",
        ),
    )
    op.create_index(
        "idx_telegram_deliveries_plan_scope_created",
        "telegram_deliveries",
        ["plan_key", "market_scope", "created_at"],
        unique=False,
    )
    # 首次过滤证据是历史审计事实；约束阻止总体状态与逐项决定互相矛盾。
    op.create_table(
        "china_announcement_matches",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("china_announcement_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_key", sa.Text(), nullable=False),
        sa.Column("discovery_type", sa.Text(), nullable=False),
        sa.Column("market_scope", sa.Text(), nullable=False),
        sa.Column("query_exchange", sa.Text(), nullable=True),
        sa.Column("query_stock_code", sa.Text(), nullable=True),
        sa.Column("query_provider_key", sa.Text(), nullable=True),
        sa.Column(
            "matched_search_keywords", postgresql.ARRAY(sa.Text()), nullable=False
        ),
        sa.Column("filter_status", sa.Text(), nullable=False),
        sa.Column(
            "filter_decisions", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("hit_count", sa.BigInteger(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "(discovery_type = 'selected_stocks' AND query_exchange IS NOT NULL AND query_stock_code IS NOT NULL AND query_provider_key IS NOT NULL AND cardinality(matched_search_keywords) = 0 AND ((market_scope = 'a_share' AND query_exchange IN ('sh', 'sz', 'bj')) OR (market_scope = 'hk' AND query_exchange = 'hk'))) OR (discovery_type = 'market_keywords' AND query_exchange IS NULL AND query_stock_code IS NULL AND query_provider_key IS NULL AND cardinality(matched_search_keywords) > 0)",
            name="ck_china_announcement_matches_discovery_shape",
        ),
        sa.CheckConstraint(
            "array_position(matched_search_keywords, NULL) IS NULL AND array_position(matched_search_keywords, '') IS NULL",
            name="ck_china_announcement_matches_keyword_values",
        ),
        sa.CheckConstraint(
            "discovery_type IN ('selected_stocks', 'market_keywords')",
            name="ck_china_announcement_matches_discovery_type",
        ),
        sa.CheckConstraint(
            "filter_status IN ('selected', 'filtered')",
            name="ck_china_announcement_matches_filter_status",
        ),
        sa.CheckConstraint(
            "market_scope IN ('a_share', 'hk')",
            name="ck_china_announcement_matches_market_scope",
        ),
        sa.CheckConstraint(
            "plan_key ~ '^[a-z][a-z0-9-]{2,63}$'",
            name="ck_china_announcement_matches_plan_key",
        ),
        sa.CheckConstraint(
            "query_provider_key IS NULL OR (query_exchange = 'sh' AND query_provider_key IN ('cninfo', 'sse')) OR (query_exchange = 'sz' AND query_provider_key IN ('cninfo', 'szse')) OR (query_exchange IN ('bj', 'hk') AND query_provider_key = 'cninfo')",
            name="ck_china_announcement_matches_provider_route",
        ),
        sa.CheckConstraint(
            "(filter_status = 'selected' AND NOT jsonb_path_exists(filter_decisions, '$[*] ? (@.outcome == \"filtered\")')) OR (filter_status = 'filtered' AND jsonb_path_exists(filter_decisions, '$[*] ? (@.outcome == \"filtered\")'))",
            name="ck_china_announcement_matches_filter_projection",
        ),
        sa.CheckConstraint(
            "hit_count >= 1", name="ck_china_announcement_matches_hit_count"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(filter_decisions) = 'array' AND jsonb_array_length(filter_decisions) > 0 AND jsonb_array_length(jsonb_path_query_array(filter_decisions, '$[*].outcome')) = jsonb_array_length(filter_decisions) AND jsonb_path_query_array(filter_decisions, '$[*].outcome') <@ '[\"selected\", \"filtered\"]'::jsonb",
            name="ck_china_announcement_matches_filter_decisions",
        ),
        sa.ForeignKeyConstraint(
            ["china_announcement_id"],
            ["china_announcements.id"],
            name="fk_china_announcement_matches_china_announcement",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "china_announcement_id",
            "plan_key",
            name="uq_china_announcement_matches_announcement_plan",
        ),
    )
    op.create_index(
        "idx_china_announcement_matches_plan_status_first_seen",
        "china_announcement_matches",
        ["plan_key", "filter_status", sa.literal_column("first_seen_at DESC")],
        unique=False,
    )
    # JSON 是权威摘要，text/tags 只是查询投影；失去一致性会让投递内容不可恢复地分叉。
    op.create_table(
        "china_summaries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("china_announcement_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("agent_key", sa.Text(), nullable=True),
        sa.Column("agent_version", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("model_provider", sa.Text(), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("summary_tags", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "summary_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("failure_log", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND agent_key IS NOT NULL AND agent_version IS NOT NULL AND prompt_version IS NOT NULL AND model_name IS NOT NULL AND summary_text IS NOT NULL AND summary_tags IS NOT NULL AND summary_result IS NOT NULL AND finished_at IS NOT NULL AND failure_reason IS NULL AND failure_log IS NULL) OR (status <> 'completed' AND summary_text IS NULL AND summary_tags IS NULL AND summary_result IS NULL AND input_tokens IS NULL AND output_tokens IS NULL)",
            name="ck_china_summaries_success_fields",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND finished_at IS NULL) OR (status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL) OR (status = 'completed' AND finished_at IS NOT NULL) OR (status IN ('failed', 'skipped') AND finished_at IS NOT NULL AND failure_reason IS NOT NULL)",
            name="ck_china_summaries_timing",
        ),
        sa.CheckConstraint(
            "(summary_result IS NULL AND summary_text IS NULL AND summary_tags IS NULL) OR (summary_result IS NOT NULL AND summary_result ->> 'schema_version' IS NOT DISTINCT FROM 'china-announcement-summary.v1' AND summary_result ->> 'summary_text' IS NOT DISTINCT FROM summary_text AND summary_result -> 'summary_tags' IS NOT DISTINCT FROM to_jsonb(summary_tags))",
            name="ck_china_summaries_result_projection",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'skipped')",
            name="ck_china_summaries_status",
        ),
        sa.CheckConstraint(
            "summary_result IS NULL OR jsonb_typeof(summary_result) = 'object'",
            name="ck_china_summaries_result_object",
        ),
        sa.CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) AND (output_tokens IS NULL OR output_tokens >= 0)",
            name="ck_china_summaries_token_counts",
        ),
        sa.CheckConstraint(
            "failure_count >= 0", name="ck_china_summaries_failure_count"
        ),
        sa.ForeignKeyConstraint(
            ["china_announcement_id"],
            ["china_announcements.id"],
            name="fk_china_summaries_china_announcement",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "china_announcement_id", name="uq_china_summaries_china_announcement"
        ),
    )
    op.create_index(
        "idx_china_summaries_claim",
        "china_summaries",
        ["status", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "cninfo_announcements",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("china_announcement_id", sa.BigInteger(), nullable=False),
        sa.Column("announcement_id", sa.Text(), nullable=False),
        sa.Column("sec_code", sa.Text(), nullable=True),
        sa.Column("sec_name", sa.Text(), nullable=True),
        sa.Column("org_id", sa.Text(), nullable=True),
        sa.Column("announcement_title", sa.Text(), nullable=True),
        sa.Column("announcement_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("adjunct_url", sa.Text(), nullable=True),
        sa.Column("adjunct_size", sa.BigInteger(), nullable=True),
        sa.Column("adjunct_type", sa.Text(), nullable=True),
        sa.Column("column_id", sa.Text(), nullable=True),
        sa.Column("page_column", sa.Text(), nullable=True),
        sa.Column("announcement_type", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "adjunct_size IS NULL OR adjunct_size >= 0",
            name="ck_cninfo_announcements_adjunct_size",
        ),
        sa.ForeignKeyConstraint(
            ["china_announcement_id"],
            ["china_announcements.id"],
            name="fk_cninfo_announcements_china_announcement",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "announcement_id", name="uq_cninfo_announcements_announcement_id"
        ),
        sa.UniqueConstraint(
            "china_announcement_id", name="uq_cninfo_announcements_china_announcement"
        ),
    )
    op.create_table(
        "sse_announcements",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("china_announcement_id", sa.BigInteger(), nullable=False),
        sa.Column("provider_announcement_id", sa.Text(), nullable=False),
        sa.Column("security_code", sa.Text(), nullable=True),
        sa.Column("security_name", sa.Text(), nullable=True),
        sa.Column("org_bulletin_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("sse_date", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("bulletin_type_desc", sa.Text(), nullable=True),
        sa.Column("is_holder_disclose", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["china_announcement_id"],
            ["china_announcements.id"],
            name="fk_sse_announcements_china_announcement",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "china_announcement_id", name="uq_sse_announcements_china_announcement"
        ),
        sa.UniqueConstraint(
            "provider_announcement_id",
            name="uq_sse_announcements_provider_announcement_id",
        ),
    )
    op.create_table(
        "szse_announcements",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("china_announcement_id", sa.BigInteger(), nullable=False),
        sa.Column("provider_announcement_id", sa.Text(), nullable=False),
        sa.Column("ann_id", sa.Text(), nullable=True),
        sa.Column("source_record_id", sa.Text(), nullable=True),
        sa.Column("sec_codes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("sec_names", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("publish_time", sa.Text(), nullable=True),
        sa.Column("attach_path", sa.Text(), nullable=True),
        sa.Column("attach_format", sa.Text(), nullable=True),
        sa.Column("attach_size", sa.BigInteger(), nullable=True),
        sa.Column("bond_type", sa.Text(), nullable=True),
        sa.Column("big_industry_code", sa.Text(), nullable=True),
        sa.Column("big_category_id", sa.Text(), nullable=True),
        sa.Column("small_category_id", sa.Text(), nullable=True),
        sa.Column("channel_code", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "array_position(sec_codes, NULL) IS NULL AND array_position(sec_names, NULL) IS NULL",
            name="ck_szse_announcements_security_arrays",
        ),
        sa.CheckConstraint(
            "attach_size IS NULL OR attach_size >= 0",
            name="ck_szse_announcements_attach_size",
        ),
        sa.ForeignKeyConstraint(
            ["china_announcement_id"],
            ["china_announcements.id"],
            name="fk_szse_announcements_china_announcement",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "china_announcement_id", name="uq_szse_announcements_china_announcement"
        ),
        sa.UniqueConstraint(
            "provider_announcement_id",
            name="uq_szse_announcements_provider_announcement_id",
        ),
    )
    # child 唯一键冻结首次 payload，避免恢复或竞态把同一逻辑消息覆盖成另一份内容。
    op.create_table(
        "telegram_document_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("telegram_delivery_id", sa.BigInteger(), nullable=False),
        sa.Column("document_key", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("storage_relative_path", sa.Text(), nullable=False),
        sa.Column("document_filename", sa.Text(), nullable=False),
        sa.Column("document_mime_type", sa.Text(), nullable=False),
        sa.Column("document_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("document_sha256", sa.Text(), nullable=False),
        sa.Column("document_caption", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("result_message_thread_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_message_url", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("failure_log", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND failure_reason IS NULL AND failure_log IS NULL) OR (status = 'running' AND started_at IS NOT NULL AND failure_reason IS NULL AND failure_log IS NULL) OR (status = 'sent' AND started_at IS NOT NULL) OR (status IN ('failed', 'unknown') AND started_at IS NOT NULL AND failure_reason IS NOT NULL)",
            name="ck_telegram_document_messages_lifecycle",
        ),
        sa.CheckConstraint(
            "(status = 'sent' AND result_chat_id IS NOT NULL AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL AND failure_reason IS NULL AND failure_log IS NULL) OR (status <> 'sent' AND result_chat_id IS NULL AND result_message_thread_id IS NULL AND telegram_message_id IS NULL AND telegram_message_url IS NULL AND sent_at IS NULL)",
            name="ck_telegram_document_messages_result_fields",
        ),
        sa.CheckConstraint(
            "document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_telegram_document_messages_document_sha256",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'sent', 'failed', 'unknown')",
            name="ck_telegram_document_messages_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_telegram_document_messages_attempt_count"
        ),
        sa.CheckConstraint(
            "document_size_bytes > 0",
            name="ck_telegram_document_messages_document_size",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_delivery_id"],
            ["telegram_deliveries.id"],
            name="fk_telegram_document_messages_delivery",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_delivery_id",
            "document_key",
            name="uq_telegram_document_messages_delivery_document",
        ),
    )
    op.create_index(
        "idx_telegram_document_messages_claim",
        "telegram_document_messages",
        ["status", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "telegram_summary_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("telegram_delivery_id", sa.BigInteger(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("result_message_thread_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_message_url", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("failure_log", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND failure_reason IS NULL AND failure_log IS NULL) OR (status = 'running' AND started_at IS NOT NULL AND failure_reason IS NULL AND failure_log IS NULL) OR (status = 'sent' AND started_at IS NOT NULL) OR (status IN ('failed', 'unknown') AND started_at IS NOT NULL AND failure_reason IS NOT NULL)",
            name="ck_telegram_summary_messages_lifecycle",
        ),
        sa.CheckConstraint(
            "(status = 'sent' AND result_chat_id IS NOT NULL AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL AND failure_reason IS NULL AND failure_log IS NULL) OR (status <> 'sent' AND result_chat_id IS NULL AND result_message_thread_id IS NULL AND telegram_message_id IS NULL AND telegram_message_url IS NULL AND sent_at IS NULL)",
            name="ck_telegram_summary_messages_result_fields",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'sent', 'failed', 'unknown')",
            name="ck_telegram_summary_messages_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_telegram_summary_messages_attempt_count"
        ),
        sa.ForeignKeyConstraint(
            ["telegram_delivery_id"],
            ["telegram_deliveries.id"],
            name="fk_telegram_summary_messages_delivery",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_delivery_id", name="uq_telegram_summary_messages_delivery"
        ),
    )
    op.create_index(
        "idx_telegram_summary_messages_claim",
        "telegram_summary_messages",
        ["status", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    """降级 schema。"""
    op.drop_index(
        "idx_telegram_summary_messages_claim", table_name="telegram_summary_messages"
    )
    op.drop_table("telegram_summary_messages")
    op.drop_index(
        "idx_telegram_document_messages_claim", table_name="telegram_document_messages"
    )
    op.drop_table("telegram_document_messages")
    op.drop_table("szse_announcements")
    op.drop_table("sse_announcements")
    op.drop_table("cninfo_announcements")
    op.drop_index("idx_china_summaries_claim", table_name="china_summaries")
    op.drop_table("china_summaries")
    op.drop_index(
        "idx_china_announcement_matches_plan_status_first_seen",
        table_name="china_announcement_matches",
    )
    op.drop_table("china_announcement_matches")
    op.drop_index(
        "idx_telegram_deliveries_plan_scope_created", table_name="telegram_deliveries"
    )
    op.drop_table("telegram_deliveries")
    op.drop_index(
        "idx_china_announcements_stock_codes_gin",
        table_name="china_announcements",
        postgresql_using="gin",
    )
    op.drop_index(
        "idx_china_announcements_published_at", table_name="china_announcements"
    )
    op.drop_table("china_announcements")
