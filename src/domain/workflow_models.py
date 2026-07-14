from __future__ import annotations

from pathlib import Path

from cninfo_announcement.models import BusinessAnnouncement
from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain.common import (
    AnnouncementSource,
    DeliveryStatus,
    Market,
    SummaryStatus,
    build_announcement_key,
    normalize_required_text,
)
from domain.summary_models import AnnouncementSummary
from domain.telegram_models import TelegramTargetKey


class AnnouncementRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: AnnouncementSource
    announcement_id: str

    @field_validator("announcement_id", mode="before")
    @classmethod
    def _normalize_announcement_id(cls, value: object) -> str:
        return normalize_required_text(value, field_name="announcement_id")

    @property
    def key(self) -> str:
        return build_announcement_key(
            source=self.source,
            announcement_id=self.announcement_id,
        )


class SyncSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fetched_count: int = 0
    skipped_count: int = 0
    inserted_announcements: int = 0
    updated_announcements: int = 0
    inserted_hits: int = 0
    updated_hits: int = 0
    filtered_hits: int = 0
    seeded_summaries: int = 0
    new_refs: list[AnnouncementRef] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class WorkflowCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: AnnouncementSource
    announcement_id: str
    announcement: BusinessAnnouncement
    market: Market
    stock_code: str
    stock_key: str
    company_name: str
    primary_hit_id: int | None = None
    search_keyword: str | None = None
    summary_status: SummaryStatus | None = None
    summary_failure_count: int = 0
    pdf_local_path: Path | None = None
    summary_json: dict[str, object] | None = None
    summary_text: str | None = None
    summary_tags: list[str] = Field(default_factory=list)
    delivery_id: int | None = None
    delivery_status: DeliveryStatus | None = None
    target_key: TelegramTargetKey | None = None
    target_chat_id: int | None = None
    target_message_thread_id: int | None = None
    text_message_id: int | None = None
    pdf_message_id: int | None = None

    @property
    def ref(self) -> AnnouncementRef:
        return AnnouncementRef(
            source=self.source,
            announcement_id=self.announcement_id,
        )

    @property
    def stored_summary(self) -> AnnouncementSummary | None:
        """只有库里的摘要字段满足投递要求时，才返回可用摘要。"""
        if self.summary_text is None:
            return None
        if len(self.summary_tags) < 3 or len(self.summary_tags) > 6:
            return None
        return AnnouncementSummary(summary=self.summary_text, tags=self.summary_tags)


class PipelineStageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    unknown_count: int = 0
