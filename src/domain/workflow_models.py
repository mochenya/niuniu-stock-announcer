from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain.common import (
    AnnouncementSource,
    build_announcement_key,
    normalize_required_text,
)


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


class PipelineStageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    unknown_count: int = 0
