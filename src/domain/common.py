from __future__ import annotations

from typing import Literal, get_args

# 这些 Literal 是当前版本的业务边界，新增市场或状态时先改这里。
AnnouncementSource = Literal["cninfo", "sse", "szse"]
Market = Literal["sh", "sz", "bj", "hk"]
SearchMode = Literal["stock", "stock_keyword"]
SummaryStatus = Literal["pending", "running", "completed", "failed", "skipped"]
DeliveryStatus = Literal["pending", "running", "completed", "failed", "unknown"]
DeliveryFailureStatus = Literal["failed", "unknown"]

SUPPORTED_ANNOUNCEMENT_SOURCES = frozenset[str](get_args(AnnouncementSource))
DEFAULT_ANNOUNCEMENT_SOURCE_BY_MARKET: dict[Market, AnnouncementSource] = {
    "hk": "cninfo",
    "bj": "cninfo",
    "sh": "sse",
    "sz": "szse",
}
ALLOWED_ANNOUNCEMENT_SOURCES_BY_MARKET: dict[Market, frozenset[AnnouncementSource]] = {
    "sh": frozenset({"cninfo", "sse"}),
    "sz": frozenset({"cninfo", "szse"}),
    "bj": frozenset({"cninfo"}),
    "hk": frozenset({"cninfo"}),
}


def build_stock_key(*, market: Market | str, stock_code: str) -> str:
    return f"{market}:{stock_code}"


def build_announcement_key(
    *, source: AnnouncementSource | str, announcement_id: str
) -> str:
    return f"{source}:{announcement_id}"


def normalize_announcement_source(
    value: AnnouncementSource | str | object,
) -> AnnouncementSource:
    """把第三方模型或配置字符串中的来源值规范化成内部 Literal。"""
    raw_value = getattr(value, "value", value)
    normalized = str(raw_value).strip().lower()
    if normalized not in SUPPORTED_ANNOUNCEMENT_SOURCES:
        raise ValueError(f"unsupported announcement source: {value}")
    return normalized  # type: ignore[return-value]


def validate_announcement_source_for_market(
    *,
    market: Market,
    source: AnnouncementSource | str | object,
) -> AnnouncementSource:
    """校验市场和公告源组合，避免配置出上游 SDK 不支持的路由。"""
    normalized_source = normalize_announcement_source(source)
    allowed_sources = ALLOWED_ANNOUNCEMENT_SOURCES_BY_MARKET[market]
    if normalized_source not in allowed_sources:
        allowed_text = ", ".join(sorted(allowed_sources))
        raise ValueError(
            f"unsupported announcement source for market {market}: "
            f"{normalized_source}. Expected one of: {allowed_text}"
        )
    return normalized_source


def normalize_text_list(value: object, *, field_name: str) -> list[str]:
    """规范化配置里的字符串列表，并保持原有顺序去重。"""
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list")
    normalized_items: list[str] = []
    seen_items: set[str] = set()
    for item in value:
        normalized_item = normalize_required_text(item, field_name=field_name)
        if normalized_item in seen_items:
            continue
        seen_items.add(normalized_item)
        normalized_items.append(normalized_item)
    return normalized_items


def normalize_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value.strip()


def normalize_required_text(value: object, *, field_name: str) -> str:
    normalized = normalize_text(value, field_name=field_name)
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


def normalize_optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = normalize_text(value, field_name=field_name)
    return normalized or None
