"""精选股票计划的按代码 discovery strategy。"""

from __future__ import annotations

from datetime import date, timedelta

from niuniu_stock_announcer.announcements.schema import AnnouncementQuery
from niuniu_stock_announcer.pipelines.china.discovery.schema import (
    DiscoveryQueryTask,
)
from niuniu_stock_announcer.pipelines.china.schema import SelectedStocksPlan


def compile_selected_stock_tasks(
    plan: SelectedStocksPlan,
    *,
    end_date: date,
    limit: int | None = None,
) -> tuple[DiscoveryQueryTask, ...]:
    """把精选 Plan 编译成只按 `(exchange, stock_code)` 查询的任务。

    Args:
        plan: 已校验且不含单股 Provider/关键词覆盖的精选 Plan。
        end_date: 本轮 discovery 的闭区间结束日期。
        limit: 可选运行时结果上限，不参与任何业务身份。

    Returns:
        保持 Plan scope/stock 顺序的独立查询任务。
    """
    start_date = end_date - timedelta(days=plan.window_days - 1)
    tasks: list[DiscoveryQueryTask] = []
    for scope, scope_plan in plan.market_scopes.items():
        for stock in scope_plan.stocks:
            provider_key = getattr(plan.announcement_providers, stock.exchange)
            tasks.append(
                DiscoveryQueryTask(
                    plan_key=plan.plan_key,
                    discovery_type="selected_stocks",
                    market_scope=scope,
                    provider_key=provider_key,
                    query=AnnouncementQuery(
                        exchange=stock.exchange,
                        market_scope=scope,
                        start_date=start_date,
                        end_date=end_date,
                        stock_code=stock.stock_code,
                        limit=limit,
                    ),
                    title_exclude_keywords=(scope_plan.filters.title_exclude_keywords),
                    target=scope_plan.delivery.telegram,
                )
            )
    return tuple(tasks)
