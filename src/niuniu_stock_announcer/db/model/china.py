"""China 公告、Provider 原始记录、match 与摘要 ORM Model。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    desc,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from niuniu_stock_announcer.db.model.base import Base


class ChinaAnnouncementModel(Base):
    """保存 China Pipeline 直接消费的 Provider 公告事实。"""

    __tablename__ = "china_announcements"
    __table_args__ = (
        UniqueConstraint(
            "provider_key",
            "provider_announcement_id",
            name="uq_china_announcements_provider_identity",
        ),
        CheckConstraint(
            "provider_key IN ('cninfo', 'sse', 'szse')",
            name="ck_china_announcements_provider_key",
        ),
        CheckConstraint(
            "market_scope IN ('a_share', 'hk')",
            name="ck_china_announcements_market_scope",
        ),
        CheckConstraint(
            "cardinality(exchanges) = cardinality(stock_codes) "
            "AND cardinality(exchanges) = cardinality(stock_names)",
            name="ck_china_announcements_security_array_alignment",
        ),
        CheckConstraint(
            "array_position(exchanges, NULL) IS NULL "
            "AND array_position(exchanges, '') IS NULL "
            "AND array_position(stock_codes, NULL) IS NULL "
            "AND array_position(stock_codes, '') IS NULL "
            "AND array_position(stock_names, '') IS NULL",
            name="ck_china_announcements_security_array_values",
        ),
        CheckConstraint(
            "cardinality(exchanges) = 0 OR "
            "(market_scope = 'a_share' "
            "AND exchanges <@ ARRAY['sh', 'sz', 'bj']::text[]) OR "
            "(market_scope = 'hk' AND exchanges <@ ARRAY['hk']::text[])",
            name="ck_china_announcements_scope_exchanges",
        ),
        CheckConstraint(
            "(pdf_storage_relative_path IS NULL "
            "AND pdf_size_bytes IS NULL AND pdf_sha256 IS NULL) OR "
            "(pdf_storage_relative_path IS NOT NULL "
            "AND pdf_size_bytes IS NOT NULL AND pdf_size_bytes > 0 "
            "AND pdf_sha256 IS NOT NULL "
            "AND pdf_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_china_announcements_pdf_snapshot",
        ),
        Index(
            "idx_china_announcements_published_at",
            desc("published_at"),
        ),
        Index(
            "idx_china_announcements_stock_codes_gin",
            "stock_codes",
            postgresql_using="gin",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    provider_key: Mapped[str] = mapped_column(Text, nullable=False)
    provider_announcement_id: Mapped[str] = mapped_column(Text, nullable=False)
    market_scope: Mapped[str] = mapped_column(Text, nullable=False)
    exchanges: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    stock_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    stock_names: Mapped[list[str | None]] = mapped_column(ARRAY(Text), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    pdf_storage_relative_path: Mapped[str | None] = mapped_column(Text)
    pdf_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    pdf_sha256: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CninfoAnnouncementModel(Base):
    """保存 CNInfo 最近一次看见的显式来源快照。"""

    __tablename__ = "cninfo_announcements"
    __table_args__ = (
        UniqueConstraint(
            "china_announcement_id",
            name="uq_cninfo_announcements_china_announcement",
        ),
        UniqueConstraint(
            "announcement_id", name="uq_cninfo_announcements_announcement_id"
        ),
        CheckConstraint(
            "adjunct_size IS NULL OR adjunct_size >= 0",
            name="ck_cninfo_announcements_adjunct_size",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    china_announcement_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "china_announcements.id",
            ondelete="RESTRICT",
            name="fk_cninfo_announcements_china_announcement",
        ),
        nullable=False,
    )
    announcement_id: Mapped[str] = mapped_column(Text, nullable=False)
    sec_code: Mapped[str | None] = mapped_column(Text)
    sec_name: Mapped[str | None] = mapped_column(Text)
    org_id: Mapped[str | None] = mapped_column(Text)
    announcement_title: Mapped[str | None] = mapped_column(Text)
    announcement_time_ms: Mapped[int | None] = mapped_column(BigInteger)
    adjunct_url: Mapped[str | None] = mapped_column(Text)
    adjunct_size: Mapped[int | None] = mapped_column(BigInteger)
    adjunct_type: Mapped[str | None] = mapped_column(Text)
    column_id: Mapped[str | None] = mapped_column(Text)
    page_column: Mapped[str | None] = mapped_column(Text)
    announcement_type: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SseAnnouncementModel(Base):
    """保存 SSE 最近一次看见的显式来源快照。"""

    __tablename__ = "sse_announcements"
    __table_args__ = (
        UniqueConstraint(
            "china_announcement_id", name="uq_sse_announcements_china_announcement"
        ),
        UniqueConstraint(
            "provider_announcement_id",
            name="uq_sse_announcements_provider_announcement_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    china_announcement_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "china_announcements.id",
            ondelete="RESTRICT",
            name="fk_sse_announcements_china_announcement",
        ),
        nullable=False,
    )
    provider_announcement_id: Mapped[str] = mapped_column(Text, nullable=False)
    security_code: Mapped[str | None] = mapped_column(Text)
    security_name: Mapped[str | None] = mapped_column(Text)
    org_bulletin_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    sse_date: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    bulletin_type_desc: Mapped[str | None] = mapped_column(Text)
    is_holder_disclose: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SzseAnnouncementModel(Base):
    """保存 SZSE 最近一次看见的显式来源快照。"""

    __tablename__ = "szse_announcements"
    __table_args__ = (
        UniqueConstraint(
            "china_announcement_id",
            name="uq_szse_announcements_china_announcement",
        ),
        UniqueConstraint(
            "provider_announcement_id",
            name="uq_szse_announcements_provider_announcement_id",
        ),
        CheckConstraint(
            "array_position(sec_codes, NULL) IS NULL "
            "AND array_position(sec_names, NULL) IS NULL",
            name="ck_szse_announcements_security_arrays",
        ),
        CheckConstraint(
            "attach_size IS NULL OR attach_size >= 0",
            name="ck_szse_announcements_attach_size",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    china_announcement_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "china_announcements.id",
            ondelete="RESTRICT",
            name="fk_szse_announcements_china_announcement",
        ),
        nullable=False,
    )
    provider_announcement_id: Mapped[str] = mapped_column(Text, nullable=False)
    ann_id: Mapped[str | None] = mapped_column(Text)
    source_record_id: Mapped[str | None] = mapped_column(Text)
    sec_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    sec_names: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    publish_time: Mapped[str | None] = mapped_column(Text)
    attach_path: Mapped[str | None] = mapped_column(Text)
    attach_format: Mapped[str | None] = mapped_column(Text)
    attach_size: Mapped[int | None] = mapped_column(BigInteger)
    bond_type: Mapped[str | None] = mapped_column(Text)
    big_industry_code: Mapped[str | None] = mapped_column(Text)
    big_category_id: Mapped[str | None] = mapped_column(Text)
    small_category_id: Mapped[str | None] = mapped_column(Text)
    channel_code: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChinaAnnouncementMatchModel(Base):
    """冻结一个 Plan 对公告的首次过滤决定和发现证据。"""

    __tablename__ = "china_announcement_matches"
    __table_args__ = (
        UniqueConstraint(
            "china_announcement_id",
            "plan_key",
            name="uq_china_announcement_matches_announcement_plan",
        ),
        CheckConstraint(
            "plan_key ~ '^[a-z][a-z0-9-]{2,63}$'",
            name="ck_china_announcement_matches_plan_key",
        ),
        CheckConstraint(
            "discovery_type IN ('selected_stocks', 'market_keywords')",
            name="ck_china_announcement_matches_discovery_type",
        ),
        CheckConstraint(
            "market_scope IN ('a_share', 'hk')",
            name="ck_china_announcement_matches_market_scope",
        ),
        CheckConstraint(
            "filter_status IN ('selected', 'filtered')",
            name="ck_china_announcement_matches_filter_status",
        ),
        CheckConstraint(
            "hit_count >= 1", name="ck_china_announcement_matches_hit_count"
        ),
        CheckConstraint(
            "jsonb_typeof(filter_decisions) = 'array' "
            "AND jsonb_array_length(filter_decisions) > 0 "
            "AND jsonb_array_length(jsonb_path_query_array("
            "filter_decisions, '$[*].outcome')) = "
            "jsonb_array_length(filter_decisions) "
            "AND jsonb_path_query_array(filter_decisions, '$[*].outcome') "
            '<@ \'["selected", "filtered"]\'::jsonb',
            name="ck_china_announcement_matches_filter_decisions",
        ),
        CheckConstraint(
            "(filter_status = 'selected' AND NOT jsonb_path_exists("
            "filter_decisions, '$[*] ? (@.outcome == \"filtered\")')) OR "
            "(filter_status = 'filtered' AND jsonb_path_exists("
            "filter_decisions, '$[*] ? (@.outcome == \"filtered\")'))",
            name="ck_china_announcement_matches_filter_projection",
        ),
        CheckConstraint(
            "array_position(matched_search_keywords, NULL) IS NULL "
            "AND array_position(matched_search_keywords, '') IS NULL",
            name="ck_china_announcement_matches_keyword_values",
        ),
        CheckConstraint(
            "(discovery_type = 'selected_stocks' "
            "AND query_exchange IS NOT NULL "
            "AND query_stock_code IS NOT NULL "
            "AND query_provider_key IS NOT NULL "
            "AND cardinality(matched_search_keywords) = 0 "
            "AND ((market_scope = 'a_share' "
            "AND query_exchange IN ('sh', 'sz', 'bj')) "
            "OR (market_scope = 'hk' AND query_exchange = 'hk'))) OR "
            "(discovery_type = 'market_keywords' "
            "AND query_exchange IS NULL AND query_stock_code IS NULL "
            "AND query_provider_key IS NULL "
            "AND cardinality(matched_search_keywords) > 0)",
            name="ck_china_announcement_matches_discovery_shape",
        ),
        CheckConstraint(
            "query_provider_key IS NULL OR "
            "(query_exchange = 'sh' AND query_provider_key IN ('cninfo', 'sse')) OR "
            "(query_exchange = 'sz' AND query_provider_key IN ('cninfo', 'szse')) OR "
            "(query_exchange IN ('bj', 'hk') AND query_provider_key = 'cninfo')",
            name="ck_china_announcement_matches_provider_route",
        ),
        Index(
            "idx_china_announcement_matches_plan_status_first_seen",
            "plan_key",
            "filter_status",
            desc("first_seen_at"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    china_announcement_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "china_announcements.id",
            ondelete="RESTRICT",
            name="fk_china_announcement_matches_china_announcement",
        ),
        nullable=False,
    )
    plan_key: Mapped[str] = mapped_column(Text, nullable=False)
    discovery_type: Mapped[str] = mapped_column(Text, nullable=False)
    market_scope: Mapped[str] = mapped_column(Text, nullable=False)
    query_exchange: Mapped[str | None] = mapped_column(Text)
    query_stock_code: Mapped[str | None] = mapped_column(Text)
    query_provider_key: Mapped[str | None] = mapped_column(Text)
    matched_search_keywords: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False
    )
    filter_status: Mapped[str] = mapped_column(Text, nullable=False)
    filter_decisions: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    hit_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="1"
    )


class ChinaSummaryModel(Base):
    """保存公告级唯一自动摘要任务及权威结果。"""

    __tablename__ = "china_summaries"
    __table_args__ = (
        UniqueConstraint(
            "china_announcement_id", name="uq_china_summaries_china_announcement"
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'skipped')",
            name="ck_china_summaries_status",
        ),
        CheckConstraint("failure_count >= 0", name="ck_china_summaries_failure_count"),
        CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) "
            "AND (output_tokens IS NULL OR output_tokens >= 0)",
            name="ck_china_summaries_token_counts",
        ),
        CheckConstraint(
            "summary_result IS NULL OR jsonb_typeof(summary_result) = 'object'",
            name="ck_china_summaries_result_object",
        ),
        CheckConstraint(
            "(summary_result IS NULL AND summary_text IS NULL "
            "AND summary_tags IS NULL) OR "
            "(summary_result IS NOT NULL "
            "AND summary_result ->> 'schema_version' "
            "IS NOT DISTINCT FROM 'china-announcement-summary.v1' "
            "AND summary_result ->> 'summary_text' "
            "IS NOT DISTINCT FROM summary_text "
            "AND summary_result -> 'summary_tags' "
            "IS NOT DISTINCT FROM to_jsonb(summary_tags))",
            name="ck_china_summaries_result_projection",
        ),
        CheckConstraint(
            "(status = 'completed' AND agent_key IS NOT NULL "
            "AND agent_version IS NOT NULL AND prompt_version IS NOT NULL "
            "AND model_name IS NOT NULL AND summary_text IS NOT NULL "
            "AND summary_tags IS NOT NULL AND summary_result IS NOT NULL "
            "AND finished_at IS NOT NULL AND failure_reason IS NULL "
            "AND failure_log IS NULL) OR "
            "(status <> 'completed' AND summary_text IS NULL "
            "AND summary_tags IS NULL AND summary_result IS NULL "
            "AND input_tokens IS NULL AND output_tokens IS NULL)",
            name="ck_china_summaries_success_fields",
        ),
        CheckConstraint(
            "(status = 'pending' AND finished_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL "
            "AND finished_at IS NULL) OR "
            "(status = 'completed' AND finished_at IS NOT NULL) OR "
            "(status IN ('failed', 'skipped') AND finished_at IS NOT NULL "
            "AND failure_reason IS NOT NULL)",
            name="ck_china_summaries_timing",
        ),
        Index("idx_china_summaries_claim", "status", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    china_announcement_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "china_announcements.id",
            ondelete="RESTRICT",
            name="fk_china_summaries_china_announcement",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    agent_key: Mapped[str | None] = mapped_column(Text)
    agent_version: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    model_provider: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    summary_text: Mapped[str | None] = mapped_column(Text)
    summary_tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    summary_result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    failure_log: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
