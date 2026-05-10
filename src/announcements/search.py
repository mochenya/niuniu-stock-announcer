from __future__ import annotations

from datetime import date

from announcements.sources import (
    announcement_source_for_market,
)
from domain.config_models import (
    StockConfig,
    WatchlistConfig,
)
from domain.search_models import (
    SearchTask,
)
from filters import combine_keywords


def build_search_tasks(config: WatchlistConfig) -> list[SearchTask]:
    """把观察列表展开成可直接执行的公告源查询任务。

    每个股票关键词会生成独立任务，便于同步阶段按任务独立提交和记录失败。
    """
    tasks: list[SearchTask] = []
    for stock in config.stocks:
        exclude_keywords = combine_keywords(
            config.filters.title_exclude_keywords,
            stock.exclude_keywords,
        )
        keywords = stock.keywords or [None]
        for keyword in keywords:
            tasks.append(
                _build_task(
                    stock,
                    search_keyword=keyword,
                    title_exclude_keywords=exclude_keywords,
                )
            )
    return tasks


def query_search_task(
    client: object,
    task: SearchTask,
    *,
    start_date: date,
    end_date: date,
):
    """按公告源客户端各自的查询接口执行同一个 SearchTask。

    上层按公告源复用 client；这里只负责把统一任务转换成各源 API 参数。
    """
    kwargs: dict[str, str | date] = {
        "stock": task.stock_code,
        "start_date": start_date,
        "end_date": end_date,
    }
    if task.search_keyword is not None:
        kwargs["searchkey"] = task.search_keyword
    if task.announcement_source == "cninfo":
        return client.query_announcements(task.market, **kwargs)
    if task.announcement_source == "sse":
        if task.market != "sh":
            raise ValueError("SSE announcement source only supports market=sh")
        return client.query_announcements(**kwargs)
    if task.announcement_source == "szse":
        if task.market != "sz":
            raise ValueError("SZSE announcement source only supports market=sz")
        return client.query_announcements(**kwargs)
    raise ValueError(f"unsupported announcement source: {task.announcement_source}")


def _build_task(
    stock: StockConfig,
    *,
    search_keyword: str | None,
    title_exclude_keywords: list[str],
) -> SearchTask:
    announcement_source = announcement_source_for_market(stock.market)
    search_mode = "stock" if search_keyword is None else "stock_keyword"
    # 这里的 source_key 标识“配置中的一次查询”，不是返回的公告。
    # 这样重复同步可以更新同一条命中记录，同时保留是哪只股票和哪个关键词命中了公告。
    source_key = "::".join(
        [
            announcement_source,
            stock.market,
            search_mode,
            stock.code,
            search_keyword or "-",
        ]
    )
    return SearchTask(
        announcement_source=announcement_source,
        source_key=source_key,
        market=stock.market,
        stock_code=stock.code,
        stock_key=stock.stock_key,
        search_mode=search_mode,
        search_keyword=search_keyword,
        title_exclude_keywords=title_exclude_keywords,
        config_snapshot=_build_config_snapshot(
            stock,
            title_exclude_keywords=title_exclude_keywords,
        ),
    )


def _build_config_snapshot(
    stock: StockConfig,
    *,
    title_exclude_keywords: list[str],
) -> dict[str, object]:
    """把实际生效的查询配置保存成可写入 JSONB 的结构。"""
    return {
        "stock": stock.model_dump(mode="json"),
        "title_exclude_keywords": title_exclude_keywords,
    }
