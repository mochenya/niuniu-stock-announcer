"""中国市场 discovery Plan 的业务 Schema。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Exchange = Literal["sh", "sz", "bj", "hk"]
MarketScope = Literal["a_share", "hk"]
AnnouncementProvider = Literal["cninfo", "sse", "szse"]
DiscoveryType = Literal["selected_stocks", "market_keywords"]

PLAN_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
ALLOWED_PROVIDERS: dict[Exchange, frozenset[AnnouncementProvider]] = {
    "sh": frozenset({"cninfo", "sse"}),
    "sz": frozenset({"cninfo", "szse"}),
    "bj": frozenset({"cninfo"}),
    "hk": frozenset({"cninfo"}),
}
SCOPE_EXCHANGES: dict[MarketScope, frozenset[Exchange]] = {
    "a_share": frozenset({"sh", "sz", "bj"}),
    "hk": frozenset({"hk"}),
}


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnnouncementProviderRoutes(_FrozenSchema):
    """按 exchange 冻结公告 Provider 路由。"""

    sh: AnnouncementProvider = "cninfo"
    sz: AnnouncementProvider = "cninfo"
    bj: AnnouncementProvider = "cninfo"
    hk: AnnouncementProvider = "cninfo"

    @model_validator(mode="after")
    def _validate_matrix(self) -> AnnouncementProviderRoutes:
        for exchange in ("sh", "sz", "bj", "hk"):
            provider = getattr(self, exchange)
            if provider not in ALLOWED_PROVIDERS[exchange]:
                allowed = ", ".join(sorted(ALLOWED_PROVIDERS[exchange]))
                raise ValueError(
                    f"announcement_providers.{exchange} 只能使用 {allowed}"
                )
        return self


class TitleFilters(_FrozenSchema):
    """保存 scope 级标题排除规则。"""

    title_exclude_keywords: tuple[str, ...] = ()

    @field_validator("title_exclude_keywords", mode="before")
    @classmethod
    def _normalize_keywords(cls, value: object) -> tuple[str, ...]:
        return _normalize_text_sequence(
            value, field_name="title_exclude_keywords", allow_empty=True
        )


class TelegramTargetPlan(_FrozenSchema):
    """描述 Plan 中尚未解析的单个 Telegram target。"""

    target_key: str
    target_url: str
    send_original_document: bool = False

    @field_validator("target_key", "target_url", mode="before")
    @classmethod
    def _normalize_text(cls, value: object, info) -> str:
        return _require_text(value, field_name=info.field_name)


class ScopeDelivery(_FrozenSchema):
    """保存一个 scope 的零或一个 Telegram target。"""

    telegram: TelegramTargetPlan | None = None


class SelectedStock(_FrozenSchema):
    """描述精选股票计划中的一只证券。"""

    exchange: Exchange
    stock_code: str
    name: str | None = None

    @field_validator("stock_code", mode="before")
    @classmethod
    def _normalize_stock_code(cls, value: object) -> str:
        return _require_text(value, field_name="stock_code")

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> str | None:
        if value is None:
            return None
        return _require_text(value, field_name="name")


class SelectedStocksScope(_FrozenSchema):
    """描述精选计划在一个 market scope 内的输入与规则。"""

    stocks: tuple[SelectedStock, ...]
    filters: TitleFilters = Field(default_factory=TitleFilters)
    delivery: ScopeDelivery = Field(default_factory=ScopeDelivery)

    @field_validator("stocks", mode="after")
    @classmethod
    def _require_stocks(
        cls, value: tuple[SelectedStock, ...]
    ) -> tuple[SelectedStock, ...]:
        if not value:
            raise ValueError("stocks 不能为空")
        return value


class KeywordDiscovery(_FrozenSchema):
    """描述全市场 discovery 使用的正向关键词。"""

    search_keywords: tuple[str, ...]

    @field_validator("search_keywords", mode="before")
    @classmethod
    def _normalize_keywords(cls, value: object) -> tuple[str, ...]:
        return _normalize_text_sequence(
            value, field_name="search_keywords", allow_empty=False
        )


class MarketKeywordsScope(_FrozenSchema):
    """描述关键词计划在一个 market scope 内的输入与规则。"""

    discovery: KeywordDiscovery
    filters: TitleFilters = Field(default_factory=TitleFilters)
    delivery: ScopeDelivery = Field(default_factory=ScopeDelivery)


class _ChinaPlanBase(_FrozenSchema):
    market: Literal["china"]
    plan_key: str
    window_days: int = Field(gt=0)
    announcement_providers: AnnouncementProviderRoutes = Field(
        default_factory=AnnouncementProviderRoutes
    )

    @field_validator("plan_key", mode="before")
    @classmethod
    def _validate_plan_key(cls, value: object) -> str:
        plan_key = _require_text(value, field_name="plan_key")
        if PLAN_KEY_PATTERN.fullmatch(plan_key) is None:
            raise ValueError("plan_key 必须匹配 ^[a-z][a-z0-9-]{2,63}$")
        return plan_key


class SelectedStocksPlan(_ChinaPlanBase):
    """精选股票 discovery Plan。"""

    plan_type: Literal["selected_stocks"]
    market_scopes: dict[MarketScope, SelectedStocksScope]

    @model_validator(mode="after")
    def _validate_scopes(self) -> SelectedStocksPlan:
        _require_market_scopes(self.market_scopes)
        seen: set[tuple[Exchange, str]] = set()
        for scope, config in self.market_scopes.items():
            for stock in config.stocks:
                if stock.exchange not in SCOPE_EXCHANGES[scope]:
                    raise ValueError(
                        f"market_scopes.{scope} 不接受 exchange={stock.exchange}"
                    )
                identity = (stock.exchange, stock.stock_code)
                if identity in seen:
                    raise ValueError(
                        f"同一 Plan 不能重复配置 ({stock.exchange}, {stock.stock_code})"
                    )
                seen.add(identity)
        return self


class MarketKeywordsPlan(_ChinaPlanBase):
    """全市场关键词 discovery Plan。"""

    plan_type: Literal["market_keywords"]
    market_scopes: dict[MarketScope, MarketKeywordsScope]

    @model_validator(mode="after")
    def _validate_scopes(self) -> MarketKeywordsPlan:
        _require_market_scopes(self.market_scopes)
        return self


ChinaPlan = SelectedStocksPlan | MarketKeywordsPlan


def _require_market_scopes(scopes: dict[MarketScope, object]) -> None:
    if not scopes:
        raise ValueError("market_scopes 不能为空")


def _normalize_text_sequence(
    value: object, *, field_name: str, allow_empty: bool
) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        # Pydantic v2 不再把 validator 抛出的 TypeError 包装为 ValidationError；配置边界统一
        # 使用 ValueError，确保 loader 能转换成不泄漏输入值的 PlanLoadError。
        raise ValueError(f"{field_name} 必须是列表")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _require_text(item, field_name=field_name)
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    if not allow_empty and not normalized:
        raise ValueError(f"{field_name} 不能为空")
    return tuple(normalized)


def _require_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    return normalized
