"""Repository 与业务层之间的冻结 Pydantic Schema。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from niuniu_stock_announcer.storage.document import StorageRelativePath

ProviderKey = Literal["cninfo", "sse", "szse"]
MarketScope = Literal["a_share", "hk"]
Exchange = Literal["sh", "sz", "bj", "hk"]
DiscoveryType = Literal["selected_stocks", "market_keywords"]
FilterStatus = Literal["selected", "filtered"]
SummaryStatus = Literal["pending", "running", "completed", "failed", "skipped"]
TelegramMessageStatus = Literal["pending", "running", "sent", "failed", "unknown"]

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PLAN_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
PROVIDER_ROUTES: dict[Exchange, frozenset[ProviderKey]] = {
    "sh": frozenset({"cninfo", "sse"}),
    "sz": frozenset({"cninfo", "szse"}),
    "bj": frozenset({"cninfo"}),
    "hk": frozenset({"cninfo"}),
}


def _require_nonblank_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("文本不能为空")
    return normalized


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class PdfSnapshot(_FrozenSchema):
    """保存一份已验证本地 PDF 的不可变身份。"""

    storage_relative_path: StorageRelativePath
    size_bytes: int = Field(gt=0)
    sha256: str

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("sha256 必须是小写 64 位十六进制文本")
        return value


class ChinaAnnouncementWrite(_FrozenSchema):
    """描述待持久化的 provider-neutral China 公告事实。"""

    provider_key: ProviderKey
    provider_announcement_id: str
    market_scope: MarketScope
    exchanges: tuple[Exchange, ...]
    stock_codes: tuple[str, ...]
    stock_names: tuple[str | None, ...]
    title: str
    published_at: datetime
    source_url: str

    @model_validator(mode="after")
    def _validate_security_projection(self) -> ChinaAnnouncementWrite:
        if not (len(self.exchanges) == len(self.stock_codes) == len(self.stock_names)):
            raise ValueError("exchanges/stock_codes/stock_names 必须按索引对齐")
        allowed = {"sh", "sz", "bj"} if self.market_scope == "a_share" else {"hk"}
        if any(exchange not in allowed for exchange in self.exchanges):
            raise ValueError("证券 exchange 与 market_scope 不一致")
        if any(not code.strip() for code in self.stock_codes):
            raise ValueError("stock_codes 不能包含空文本")
        if any(name is not None and not name.strip() for name in self.stock_names):
            raise ValueError("stock_names 只能包含非空文本或 null")
        return self

    @field_validator("provider_announcement_id", "title", "source_url")
    @classmethod
    def _require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("公告标识、标题和来源 URL 不能为空")
        return normalized

    @field_validator("published_at")
    @classmethod
    def _require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at 必须包含时区")
        return value


class ChinaAnnouncementRecord(ChinaAnnouncementWrite):
    """描述脱离 ORM Session 的 China 公告记录。"""

    id: int
    pdf: PdfSnapshot | None = None
    first_seen_at: datetime
    last_seen_at: datetime


class CninfoAnnouncementWrite(_FrozenSchema):
    """描述 CNInfo 显式来源快照。"""

    china_announcement_id: int
    announcement_id: str
    sec_code: str | None = None
    sec_name: str | None = None
    org_id: str | None = None
    announcement_title: str | None = None
    announcement_time_ms: int | None = None
    adjunct_url: str | None = None
    adjunct_size: int | None = Field(default=None, ge=0)
    adjunct_type: str | None = None
    column_id: str | None = None
    page_column: str | None = None
    announcement_type: str | None = None

    @field_validator("announcement_id")
    @classmethod
    def _normalize_announcement_id(cls, value: str) -> str:
        return _require_nonblank_text(value)


class CninfoAnnouncementRecord(CninfoAnnouncementWrite):
    """描述脱离 ORM Session 的 CNInfo 来源记录。"""

    id: int
    first_seen_at: datetime
    last_seen_at: datetime


class SseAnnouncementWrite(_FrozenSchema):
    """描述 SSE 显式来源快照。"""

    china_announcement_id: int
    provider_announcement_id: str
    security_code: str | None = None
    security_name: str | None = None
    org_bulletin_id: str | None = None
    title: str | None = None
    sse_date: str | None = None
    url: str | None = None
    bulletin_type_desc: str | None = None
    is_holder_disclose: str | None = None

    @field_validator("provider_announcement_id")
    @classmethod
    def _normalize_provider_announcement_id(cls, value: str) -> str:
        return _require_nonblank_text(value)


class SseAnnouncementRecord(SseAnnouncementWrite):
    """描述脱离 ORM Session 的 SSE 来源记录。"""

    id: int
    first_seen_at: datetime
    last_seen_at: datetime


class SzseAnnouncementWrite(_FrozenSchema):
    """描述 SZSE 显式来源快照。"""

    china_announcement_id: int
    provider_announcement_id: str
    ann_id: str | None = None
    source_record_id: str | None = None
    sec_codes: tuple[str, ...] = ()
    sec_names: tuple[str, ...] = ()
    title: str | None = None
    publish_time: str | None = None
    attach_path: str | None = None
    attach_format: str | None = None
    attach_size: int | None = Field(default=None, ge=0)
    bond_type: str | None = None
    big_industry_code: str | None = None
    big_category_id: str | None = None
    small_category_id: str | None = None
    channel_code: str | None = None

    @field_validator("provider_announcement_id")
    @classmethod
    def _normalize_provider_announcement_id(cls, value: str) -> str:
        return _require_nonblank_text(value)


class SzseAnnouncementRecord(SzseAnnouncementWrite):
    """描述脱离 ORM Session 的 SZSE 来源记录。"""

    id: int
    first_seen_at: datetime
    last_seen_at: datetime


class TitleFilterEvidence(_FrozenSchema):
    """保存标题排除规则实际评估的输入和命中项。"""

    evaluated_title: str
    configured_keywords: tuple[str, ...]
    matched_keywords: tuple[str, ...]


class TitleFilterDecision(_FrozenSchema):
    """保存首版标题排除规则的一次版本化决定。"""

    filter_type: Literal["title_exclusion"] = "title_exclusion"
    schema_version: Literal["v1"] = "v1"
    outcome: FilterStatus
    reason_code: Literal["passed", "excluded_keyword"]
    evidence: TitleFilterEvidence

    @model_validator(mode="after")
    def _validate_projection(self) -> TitleFilterDecision:
        filtered = bool(self.evidence.matched_keywords)
        if filtered != (self.outcome == "filtered"):
            raise ValueError("标题命中证据与 outcome 不一致")
        expected_reason = "excluded_keyword" if filtered else "passed"
        if self.reason_code != expected_reason:
            raise ValueError("标题命中证据与 reason_code 不一致")
        return self


class ChinaMatchWrite(_FrozenSchema):
    """描述一份 Plan 对公告的首次过滤决定和发现证据。"""

    china_announcement_id: int
    plan_key: str
    discovery_type: DiscoveryType
    market_scope: MarketScope
    query_exchange: Exchange | None = None
    query_stock_code: str | None = None
    query_provider_key: ProviderKey | None = None
    matched_search_keywords: tuple[str, ...] = ()
    filter_status: FilterStatus
    filter_decisions: tuple[TitleFilterDecision, ...]

    @model_validator(mode="after")
    def _validate_shape(self) -> ChinaMatchWrite:
        if PLAN_KEY_PATTERN.fullmatch(self.plan_key) is None:
            raise ValueError("plan_key 必须匹配 ^[a-z][a-z0-9-]{2,63}$")
        if not self.filter_decisions:
            raise ValueError("filter_decisions 不能为空")
        has_filtered = any(
            decision.outcome == "filtered" for decision in self.filter_decisions
        )
        if has_filtered != (self.filter_status == "filtered"):
            raise ValueError("filter_decisions 与 filter_status 不一致")
        if self.discovery_type == "selected_stocks":
            if None in (
                self.query_exchange,
                self.query_stock_code,
                self.query_provider_key,
            ):
                raise ValueError("selected_stocks 必须保存完整 query 证据")
            if self.matched_search_keywords:
                raise ValueError("selected_stocks 不保存 search keyword")
            if not self.query_stock_code or not self.query_stock_code.strip():
                raise ValueError("selected_stocks 的 query_stock_code 不能为空")
            allowed_exchanges = (
                {"sh", "sz", "bj"} if self.market_scope == "a_share" else {"hk"}
            )
            if self.query_exchange not in allowed_exchanges:
                raise ValueError("query_exchange 与 market_scope 不一致")
            if self.query_provider_key not in PROVIDER_ROUTES[self.query_exchange]:
                raise ValueError("query_provider_key 与 query_exchange 不一致")
        elif (
            any(
                value is not None
                for value in (
                    self.query_exchange,
                    self.query_stock_code,
                    self.query_provider_key,
                )
            )
            or not self.matched_search_keywords
        ):
            raise ValueError("market_keywords 只能保存非空 search keyword 证据")
        if any(not keyword.strip() for keyword in self.matched_search_keywords):
            raise ValueError("matched_search_keywords 不能包含空文本")
        if len(set(self.matched_search_keywords)) != len(self.matched_search_keywords):
            raise ValueError("matched_search_keywords 不能重复")
        return self


class ChinaMatchRecord(ChinaMatchWrite):
    """描述脱离 ORM Session 的 China match 记录。"""

    id: int
    first_seen_at: datetime
    last_seen_at: datetime
    hit_count: int


class ChinaMatchPersistResult(_FrozenSchema):
    """区分本轮首次创建与重复发现的 match 写入结果。"""

    record: ChinaMatchRecord
    created: bool


class ChinaSummaryResult(_FrozenSchema):
    """保存 China Agent 输出的权威 JSON 结构。"""

    schema_version: Literal["china-announcement-summary.v1"] = (
        "china-announcement-summary.v1"
    )
    summary_text: str
    summary_tags: tuple[str, ...]

    @field_validator("summary_text")
    @classmethod
    def _require_summary_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("summary_text 不能为空")
        return normalized


class ChinaSummaryRecord(_FrozenSchema):
    """描述脱离 ORM Session 的公告摘要任务。"""

    id: int
    china_announcement_id: int
    status: SummaryStatus
    failure_count: int
    agent_key: str | None
    agent_version: str | None
    prompt_version: str | None
    model_provider: str | None
    model_name: str | None
    input_tokens: int | None
    output_tokens: int | None
    result: ChinaSummaryResult | None
    failure_reason: str | None
    failure_log: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChinaSummaryClaim(_FrozenSchema):
    """保存摘要领取提交后可交给外部 Agent 的冻结输入。"""

    summary: ChinaSummaryRecord
    announcement: ChinaAnnouncementRecord


class ChinaSummaryRenderContext(_FrozenSchema):
    """保存 Delivery Service 渲染所需的 China 侧冻结数据。"""

    summary: ChinaSummaryRecord
    announcement: ChinaAnnouncementRecord
    selected_matches: tuple[ChinaMatchRecord, ...]


class SummaryCompletion(_FrozenSchema):
    """描述一次成功摘要的审计字段和权威结果。"""

    agent_key: str
    agent_version: str
    prompt_version: str
    model_provider: str | None = None
    model_name: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    result: ChinaSummaryResult

    @field_validator("agent_key", "agent_version", "prompt_version", "model_name")
    @classmethod
    def _normalize_required_text(cls, value: str) -> str:
        return _require_nonblank_text(value)


class TelegramDeliveryWrite(_FrozenSchema):
    """描述待创建的不可变 Telegram 逻辑投递快照。"""

    producer_key: str = Field(min_length=1)
    business_key: str = Field(min_length=1)
    plan_key: str = Field(min_length=1)
    market_scope: str = Field(min_length=1)
    target_key: str = Field(min_length=1)
    target_url: str = Field(min_length=1)
    target_chat_id: int
    target_message_thread_id: int | None = None
    send_original_document: bool

    @field_validator(
        "producer_key",
        "business_key",
        "plan_key",
        "market_scope",
        "target_key",
        "target_url",
    )
    @classmethod
    def _normalize_required_text(cls, value: str) -> str:
        return _require_nonblank_text(value)


class TelegramDeliveryRecord(TelegramDeliveryWrite):
    """描述脱离 ORM Session 的 Telegram 逻辑投递。"""

    id: int
    created_at: datetime


class TelegramSummaryMessageWrite(_FrozenSchema):
    """描述待物化的不可变 Telegram 文本 payload。"""

    telegram_delivery_id: int
    text_content: str = Field(min_length=1)

    @field_validator("text_content")
    @classmethod
    def _normalize_text_content(cls, value: str) -> str:
        return _require_nonblank_text(value)


class TelegramDocumentMessageWrite(_FrozenSchema):
    """描述待物化的不可变 Telegram document payload。"""

    telegram_delivery_id: int
    document_key: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    storage_relative_path: StorageRelativePath
    document_filename: str = Field(min_length=1)
    document_mime_type: str = Field(min_length=1)
    document_size_bytes: int = Field(gt=0)
    document_sha256: str
    document_caption: str

    @field_validator(
        "document_key", "source_url", "document_filename", "document_mime_type"
    )
    @classmethod
    def _normalize_required_text(cls, value: str) -> str:
        return _require_nonblank_text(value)

    @field_validator("document_sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("document_sha256 必须是小写 64 位十六进制文本")
        return value


class TelegramSummaryMessageRecord(TelegramSummaryMessageWrite):
    """描述脱离 ORM Session 的 Telegram 文本消息记录。"""

    id: int
    status: TelegramMessageStatus
    attempt_count: int
    result_chat_id: int | None
    result_message_thread_id: int | None
    telegram_message_id: int | None
    telegram_message_url: str | None
    failure_reason: str | None
    failure_log: str | None
    started_at: datetime | None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TelegramDocumentMessageRecord(TelegramDocumentMessageWrite):
    """描述脱离 ORM Session 的 Telegram document 消息记录。"""

    id: int
    status: TelegramMessageStatus
    attempt_count: int
    result_chat_id: int | None
    result_message_thread_id: int | None
    telegram_message_id: int | None
    telegram_message_url: str | None
    failure_reason: str | None
    failure_log: str | None
    started_at: datetime | None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TelegramSummaryClaim(_FrozenSchema):
    """保存文本领取提交后可直接交给 Telegram adapter 的冻结数据。"""

    message: TelegramSummaryMessageRecord
    delivery: TelegramDeliveryRecord


class TelegramDocumentClaim(_FrozenSchema):
    """保存 document 领取提交后可直接交给 Telegram adapter 的冻结数据。"""

    message: TelegramDocumentMessageRecord
    delivery: TelegramDeliveryRecord


class StaleTelegramRecovery(_FrozenSchema):
    """汇总 stale Telegram running 转为 unknown 的记录数。"""

    summary_messages: int
    document_messages: int
