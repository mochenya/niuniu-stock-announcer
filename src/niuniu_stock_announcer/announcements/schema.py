"""公告 Provider 与 China Pipeline 之间的业务契约。"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from niuniu_stock_announcer.storage.document import StorageRelativePath

ProviderKey = Literal["cninfo", "sse", "szse"]
Exchange = Literal["sh", "sz", "bj", "hk"]
MarketScope = Literal["a_share", "hk"]


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnnouncementSecurity(_FrozenSchema):
    """保存 Provider 返回的一条证券关系快照。"""

    exchange: Exchange
    stock_code: str
    stock_name: str | None = None

    @field_validator("stock_code")
    @classmethod
    def _normalize_stock_code(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("stock_code 不能为空")
        return normalized

    @field_validator("stock_name")
    @classmethod
    def _normalize_stock_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ChinaAnnouncement(_FrozenSchema):
    """跨 Provider 使用的 China 公告业务事实。"""

    provider_key: ProviderKey
    provider_announcement_id: str
    market_scope: MarketScope
    securities: tuple[AnnouncementSecurity, ...] = ()
    title: str
    published_at: datetime
    source_url: str

    @field_validator("provider_announcement_id", "title")
    @classmethod
    def _normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("公告标识和标题不能为空")
        return normalized

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, value: str) -> str:
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("source_url 必须是完整 HTTPS URL")
        return normalized

    @field_validator("published_at")
    @classmethod
    def _require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at 必须包含时区")
        return value

    @model_validator(mode="after")
    def _validate_scope_and_security_identity(self) -> ChinaAnnouncement:
        allowed = {"sh", "sz", "bj"} if self.market_scope == "a_share" else {"hk"}
        if any(security.exchange not in allowed for security in self.securities):
            raise ValueError("证券 exchange 与 market_scope 不一致")
        identities = [
            (security.exchange, security.stock_code) for security in self.securities
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("同一公告的证券关系不能重复")
        return self


class CninfoSourceSnapshot(_FrozenSchema):
    """保存 CNInfo 已审阅且允许持久化的来源字段。"""

    provider_key: Literal["cninfo"] = "cninfo"
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


class SseSourceSnapshot(_FrozenSchema):
    """保存 SSE 已审阅且允许持久化的来源字段。"""

    provider_key: Literal["sse"] = "sse"
    provider_announcement_id: str
    security_code: str | None = None
    security_name: str | None = None
    org_bulletin_id: str | None = None
    title: str | None = None
    sse_date: str | None = None
    url: str | None = None
    bulletin_type_desc: str | None = None
    is_holder_disclose: str | None = None


class SzseSourceSnapshot(_FrozenSchema):
    """保存 SZSE 已审阅且允许持久化的来源字段。"""

    provider_key: Literal["szse"] = "szse"
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


ProviderSourceSnapshot = Annotated[
    CninfoSourceSnapshot | SseSourceSnapshot | SzseSourceSnapshot,
    Field(discriminator="provider_key"),
]


class ProviderAnnouncement(_FrozenSchema):
    """把业务公告与显式来源投影绑定为一个原子持久化输入。"""

    announcement: ChinaAnnouncement
    source_snapshot: ProviderSourceSnapshot

    @model_validator(mode="after")
    def _validate_provider_pair(self) -> ProviderAnnouncement:
        if self.announcement.provider_key != self.source_snapshot.provider_key:
            raise ValueError("业务公告与来源快照 Provider 不一致")
        source_identity = (
            self.source_snapshot.announcement_id
            if isinstance(self.source_snapshot, CninfoSourceSnapshot)
            else self.source_snapshot.provider_announcement_id
        )
        if self.announcement.provider_announcement_id != source_identity:
            raise ValueError("业务公告与来源快照身份不一致")
        return self


class AnnouncementQuery(_FrozenSchema):
    """描述一次按代码或按关键词执行的有界 Provider 查询。"""

    exchange: Exchange
    market_scope: MarketScope
    start_date: date
    end_date: date
    stock_code: str | None = None
    search_keyword: str | None = None
    limit: int | None = Field(default=None, gt=0)

    @field_validator("stock_code", "search_keyword")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _validate_query_shape(self) -> AnnouncementQuery:
        if self.start_date > self.end_date:
            raise ValueError("start_date 不能晚于 end_date")
        if (self.stock_code is None) == (self.search_keyword is None):
            raise ValueError("stock_code 与 search_keyword 必须且只能提供一个")
        allowed_exchanges = (
            {"sh", "sz", "bj"} if self.market_scope == "a_share" else {"hk"}
        )
        if self.exchange not in allowed_exchanges:
            raise ValueError("query exchange 与 market_scope 不一致")
        return self


class ProviderItemError(_FrozenSchema):
    """描述 Provider 响应中单条记录的确定映射失败。"""

    item_index: int = Field(ge=0)
    error_type: str
    message: str


class ProviderQueryResult(_FrozenSchema):
    """保存一次查询的成功公告与可隔离映射失败。"""

    provider_key: ProviderKey
    items: tuple[ProviderAnnouncement, ...]
    item_errors: tuple[ProviderItemError, ...] = ()
    has_more: bool = False

    @model_validator(mode="after")
    def _validate_item_providers(self) -> ProviderQueryResult:
        if any(
            item.announcement.provider_key != self.provider_key for item in self.items
        ):
            raise ValueError("ProviderQueryResult 包含其他 Provider 公告")
        return self


class StoredAnnouncementDocument(_FrozenSchema):
    """描述一份已经过结构和路径校验的本地公告 PDF。"""

    provider_key: ProviderKey
    provider_announcement_id: str
    source_url: str
    storage_relative_path: StorageRelativePath
    local_path: Path
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(gt=0)
