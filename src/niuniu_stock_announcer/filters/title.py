"""确定性的公告标题排除策略。"""

from __future__ import annotations

from collections.abc import Sequence

from niuniu_stock_announcer.filters.schema import (
    TitleFilterDecision,
    TitleFilterEvidence,
)


def evaluate_title_filter(
    title: str, configured_keywords: Sequence[str]
) -> TitleFilterDecision:
    """按配置顺序评估标题并生成可冻结的 typed evidence。

    Args:
        title: Provider mapper 已规范化的公告标题。
        configured_keywords: 当前 Plan scope 的排除关键词。

    Returns:
        包含完整评估输入、命中词和稳定原因码的版本化决定。
    """
    normalized_keywords = tuple(
        dict.fromkeys(
            keyword.strip() for keyword in configured_keywords if keyword.strip()
        )
    )
    matched = tuple(keyword for keyword in normalized_keywords if keyword in title)
    filtered = bool(matched)
    return TitleFilterDecision(
        outcome="filtered" if filtered else "selected",
        reason_code="excluded_keyword" if filtered else "passed",
        evidence=TitleFilterEvidence(
            evaluated_title=title,
            configured_keywords=normalized_keywords,
            matched_keywords=matched,
        ),
    )
