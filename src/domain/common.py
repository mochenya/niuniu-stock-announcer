from __future__ import annotations

from typing import Literal

# 这些 Literal 是当前版本的业务边界，新增市场或状态时先改这里。
AnnouncementSource = Literal["cninfo", "sse", "szse"]
Market = Literal["sh", "sz", "bj", "hk"]
SearchMode = Literal["stock", "stock_keyword"]
WorkflowStatus = Literal["pending", "running", "completed", "failed", "unknown"]


def build_stock_key(*, market: Market | str, stock_code: str) -> str:
    return f"{market}:{stock_code}"


def build_announcement_key(
    *, source: AnnouncementSource | str, announcement_id: str
) -> str:
    return f"{source}:{announcement_id}"


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
