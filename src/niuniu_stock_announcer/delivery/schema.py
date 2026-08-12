"""Delivery Service 与 IM adapter 之间的冻结业务 Schema。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from niuniu_stock_announcer.storage.document import StorageRelativePath

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _require_nonblank_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("文本不能为空")
    return normalized


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChinaDeliveryRenderInput(_FrozenSchema):
    """保存从持久化快照投影出的 China Telegram 渲染事实。"""

    provider_key: str
    provider_announcement_id: str
    title: str
    published_at: datetime
    company_name: str | None = None
    exchange: Literal["sh", "sz", "bj", "hk"] | None = None
    stock_code: str | None = None
    discovery_type: Literal["selected_stocks", "market_keywords"]
    matched_search_keywords: tuple[str, ...] = ()
    summary_status: Literal["completed", "skipped"]
    summary_text: str | None = None
    summary_tags: tuple[str, ...] = ()

    @field_validator("provider_key", "provider_announcement_id", "title")
    @classmethod
    def _normalize_required_text(cls, value: str) -> str:
        return _require_nonblank_text(value)

    @field_validator("company_name", "stock_code")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_nonblank_text(value)

    @field_validator("published_at")
    @classmethod
    def _require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at 必须包含时区")
        return value

    @model_validator(mode="after")
    def _validate_summary_shape(self) -> ChinaDeliveryRenderInput:
        if self.summary_status == "completed":
            if self.summary_text is None or not self.summary_tags:
                raise ValueError("completed 摘要必须包含文本与标签")
        elif self.summary_text is not None or self.summary_tags:
            raise ValueError("skipped 摘要不能携带成功结果")
        return self


class DeliverySummaryPayload(_FrozenSchema):
    """描述准备一次性物化的不可变文本 payload。"""

    text_content: str = Field(min_length=1)

    @field_validator("text_content")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return _require_nonblank_text(value)


class DeliveryDocumentPayload(_FrozenSchema):
    """描述准备一次性物化的不可变本地 document payload。"""

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


class DeliveryMaterialization(_FrozenSchema):
    """汇总一个逻辑投递需要冻结的文本与 document payload。"""

    summary: DeliverySummaryPayload
    documents: tuple[DeliveryDocumentPayload, ...] = ()
