"""两种 China discovery strategy 共享的窄查询与候选契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from niuniu_stock_announcer.announcements.schema import (
    AnnouncementQuery,
    ProviderAnnouncement,
    ProviderKey,
)
from niuniu_stock_announcer.filters.schema import TitleFilterDecision
from niuniu_stock_announcer.pipelines.china.schema import (
    ALLOWED_PROVIDERS,
    DiscoveryType,
    MarketScope,
    TelegramTargetPlan,
)


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DiscoveryQueryTask(_FrozenSchema):
    """描述一个 strategy 编译出的独立 Provider 查询。"""

    plan_key: str
    discovery_type: DiscoveryType
    market_scope: MarketScope
    provider_key: ProviderKey
    query: AnnouncementQuery
    title_exclude_keywords: tuple[str, ...]
    target: TelegramTargetPlan | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> DiscoveryQueryTask:
        if self.query.market_scope != self.market_scope:
            raise ValueError("discovery task 的 query scope 不一致")
        if self.provider_key not in ALLOWED_PROVIDERS[self.query.exchange]:
            raise ValueError("discovery task 的 Provider 与 exchange 不一致")
        if self.discovery_type == "selected_stocks":
            if self.query.stock_code is None or self.query.search_keyword is not None:
                raise ValueError("selected_stocks task 必须只按 stock_code 查询")
        elif self.query.search_keyword is None or self.query.stock_code is not None:
            raise ValueError("market_keywords task 必须只按 search_keyword 查询")
        return self


class DiscoveryCandidate(_FrozenSchema):
    """描述一次查询产生的一条可独立提交公告命中。"""

    task: DiscoveryQueryTask
    provider_item: ProviderAnnouncement
    filter_decision: TitleFilterDecision
    matched_search_keywords: tuple[str, ...] = ()
    hit_increment: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _validate_provider_and_scope(self) -> DiscoveryCandidate:
        announcement = self.provider_item.announcement
        if announcement.provider_key != self.task.provider_key:
            raise ValueError("Provider 查询结果与 task 路由不一致")
        if announcement.market_scope != self.task.market_scope:
            raise ValueError("Provider 查询结果与 task market scope 不一致")
        if self.task.discovery_type == "selected_stocks":
            if self.matched_search_keywords:
                raise ValueError("selected_stocks candidate 不保存 search keyword")
        elif not self.matched_search_keywords:
            raise ValueError("market_keywords candidate 必须保存 search keyword")
        return self


SyncErrorPhase = Literal["resolve", "query", "map", "persist"]


class SyncError(_FrozenSchema):
    """保存一条可诊断且不含原始 payload 的同步错误。"""

    phase: SyncErrorPhase
    provider_key: ProviderKey
    exchange: str
    stock_code: str | None = None
    search_keyword: str | None = None
    provider_announcement_id: str | None = None
    error_type: str
    message: str


class SyncActivation(_FrozenSchema):
    """保存本轮新 selected match 实际激活的后处理引用。"""

    announcement_id: int
    match_id: int
    summary_id: int
    delivery_id: int | None = None


class SyncResult(_FrozenSchema):
    """保存提交成功后的统计、激活引用和隔离错误。"""

    queries_succeeded: int = 0
    persisted_items: int = 0
    created_matches: int = 0
    repeated_matches: int = 0
    selected_matches: int = 0
    filtered_matches: int = 0
    activations: tuple[SyncActivation, ...] = ()
    errors: tuple[SyncError, ...] = ()
