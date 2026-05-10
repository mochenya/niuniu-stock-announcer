from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from domain.common import (
    AnnouncementSource,
    Market,
    SearchMode,
)


class SearchTask(BaseModel):
    """一条从观察列表展开后的公告查询任务。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    announcement_source: AnnouncementSource
    source_key: str
    market: Market
    stock_code: str
    stock_key: str
    search_mode: SearchMode
    search_keyword: str | None = None
    title_exclude_keywords: list[str] = Field(default_factory=list)
    config_snapshot: dict[str, object] = Field(default_factory=dict)


class TitleFilterDecision(BaseModel):
    """标题过滤结果，后续会原样写入命中记录。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filtered: bool
    reason: str | None = None
    matched_keywords: list[str] = Field(default_factory=list)


class HitUpsertResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hit_id: int
    inserted: bool
    filter_status: str
