"""全市场关键词计划的 scope/关键词 discovery strategy。"""

from __future__ import annotations

from datetime import date, timedelta

from niuniu_stock_announcer.announcements.schema import AnnouncementQuery
from niuniu_stock_announcer.pipelines.china.discovery.schema import (
    DiscoveryQueryTask,
)
from niuniu_stock_announcer.pipelines.china.profile import ChinaMarketProfile
from niuniu_stock_announcer.pipelines.china.schema import MarketKeywordsPlan


def compile_market_keyword_tasks(
    plan: MarketKeywordsPlan,
    *,
    end_date: date,
    profile: ChinaMarketProfile,
    limit: int | None = None,
) -> tuple[DiscoveryQueryTask, ...]:
    """把关键词 Plan 编译成 scope 下各 exchange 的正向关键词查询。

    Args:
        plan: 已校验且按 scope 分离正向/排除关键词的 Plan。
        end_date: 本轮 discovery 的闭区间结束日期。
        profile: China scope 到 exchange 的稳定映射 owner。
        limit: 可选运行时结果上限，不参与任何业务身份。

    Returns:
        按 scope、exchange、关键词稳定排序的独立查询任务。
    """
    start_date = end_date - timedelta(days=plan.window_days - 1)
    tasks: list[DiscoveryQueryTask] = []
    for scope, scope_plan in plan.market_scopes.items():
        for exchange in profile.exchanges_for_scope(scope):
            provider_key = getattr(plan.announcement_providers, exchange)
            for keyword in scope_plan.discovery.search_keywords:
                tasks.append(
                    DiscoveryQueryTask(
                        plan_key=plan.plan_key,
                        discovery_type="market_keywords",
                        market_scope=scope,
                        provider_key=provider_key,
                        query=AnnouncementQuery(
                            exchange=exchange,
                            market_scope=scope,
                            start_date=start_date,
                            end_date=end_date,
                            search_keyword=keyword,
                            limit=limit,
                        ),
                        title_exclude_keywords=(
                            scope_plan.filters.title_exclude_keywords
                        ),
                        target=scope_plan.delivery.telegram,
                    )
                )
    return tuple(tasks)
