from __future__ import annotations

from collections.abc import Sequence

from domain.search_models import TitleFilterDecision


def decide_title_filter(
    title: str | None,
    keywords: Sequence[str],
) -> TitleFilterDecision:
    """返回单条公告标题过滤后需要落库的决策。"""
    matched_keywords = find_title_keywords(title, keywords)
    if matched_keywords:
        return TitleFilterDecision(
            filtered=True,
            reason="title_exclude_keyword",
            matched_keywords=matched_keywords,
        )
    return TitleFilterDecision(filtered=False)


def find_title_keywords(title: str | None, keywords: Sequence[str]) -> list[str]:
    """按配置顺序返回命中的标题排除关键词。"""
    if not title:
        return []
    return [keyword for keyword in keywords if keyword and keyword in title]


def combine_keywords(*groups: Sequence[str]) -> list[str]:
    """合并全局和个股关键词，并按首次出现顺序去重。"""
    combined: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for keyword in group:
            normalized = keyword.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            combined.append(normalized)
    return combined
