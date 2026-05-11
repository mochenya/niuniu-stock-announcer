from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from random import uniform
from time import sleep

from announcements.sources import (
    create_announcement_client,
    normalize_announcement_source,
)
from announcements.search import (
    build_search_tasks,
    query_search_task,
)
from config.runtime import load_runtime_config
from config.watchlist import load_watchlist_config
from db.connection import connect_database
from db.repository import AnnouncementRepository
from db.schema import ensure_schema
from domain.common import AnnouncementSource
from domain.search_models import SearchTask
from domain.workflow_models import (
    AnnouncementRef,
    SyncSummary,
)
from filters import decide_title_filter
from log.events import log_event
from workflow.common import (
    ProgressReporter,
    noop_progress,
    require_database_url,
    short_error,
)

DEFAULT_SYNC_SOURCE_DELAY_RANGE_SECONDS = (0.5, 0.85)


def sync_once(
    *,
    env_file: str | Path | None = None,
    config_file: str | Path | None = None,
    window_days: int | None = None,
    progress: ProgressReporter | None = None,
) -> SyncSummary:
    """同步观察列表公告，并为未过滤的新公告初始化后续工作流。

    每个查询任务独立提交，单个股票或关键词失败不会回滚其他任务。
    """
    report = progress or noop_progress
    runtime_config = load_runtime_config(env_file=env_file, require_database=True)
    watchlist_config = load_watchlist_config(
        config_file or runtime_config.watchlist_file
    )
    effective_window_days = (
        window_days or watchlist_config.window_days or runtime_config.window_days
    )
    window_end = date.today()
    window_start = window_end - timedelta(days=effective_window_days - 1)
    tasks = build_search_tasks(watchlist_config)
    summary = SyncSummary()
    report(
        log_event(
            "sync",
            "running",
            window=f"{window_start.isoformat()}..{window_end.isoformat()}",
            tasks=len(tasks),
        )
    )

    database_url = require_database_url(runtime_config)
    with connect_database(database_url) as conn:
        ensure_schema(conn)
        repo = AnnouncementRepository(conn)
        for source, source_tasks in _group_tasks_by_source(tasks).items():
            with create_announcement_client(source) as client:
                for index, task in enumerate(source_tasks, start=1):
                    _wait_before_same_source_query(
                        index=index,
                        delay_seconds=runtime_config.sync_source_delay_seconds,
                    )
                    report(
                        log_event(
                            "sync",
                            "querying",
                            progress=f"{index:02d}/{len(source_tasks):02d}",
                            source=source,
                            stock=task.stock_code,
                            company=_task_company_name(task),
                            keyword=task.search_keyword or "-",
                        )
                    )
                    try:
                        result = query_search_task(
                            client,
                            task,
                            start_date=window_start,
                            end_date=window_end,
                        )
                        _persist_query_result(repo, task, result, summary, report)
                        report(
                            log_event(
                                "sync",
                                "query_done",
                                progress=f"{index:02d}/{len(source_tasks):02d}",
                                source=source,
                                stock=task.stock_code,
                                company=_task_company_name(task),
                                keyword=task.search_keyword or "-",
                                fetched=len(result.response.announcements),
                                selected=len(result.items),
                            )
                        )
                        # 每个查询任务独立提交，单个股票或关键词失败不会回滚其他任务。
                        conn.commit()
                    except Exception as exc:
                        conn.rollback()
                        error_text = short_error(exc)
                        summary.errors.append(f"{task.source_key}: {error_text}")
                        report(
                            log_event(
                                "sync",
                                "query_failed",
                                level="WARNING",
                                source=source,
                                stock=task.stock_code,
                                company=_task_company_name(task),
                                keyword=task.search_keyword or "-",
                                error=error_text,
                            )
                        )
        report(
            log_event(
                "sync",
                "finished",
                fetched=summary.fetched_count,
                filtered=summary.filtered_hits,
                seeded=summary.seeded_summaries,
                errors=len(summary.errors),
            )
        )
    return summary


def _wait_before_same_source_query(
    *,
    index: int,
    delay_seconds: float | None,
) -> None:
    """同一公告源连续查询前做短暂冷却，降低被上游误判为高频请求的概率。"""
    if index <= 1:
        return
    delay = (
        uniform(*DEFAULT_SYNC_SOURCE_DELAY_RANGE_SECONDS)
        if delay_seconds is None
        else delay_seconds
    )
    if delay <= 0:
        return
    sleep(delay)


def _persist_query_result(
    repo: AnnouncementRepository,
    task: SearchTask,
    result,
    summary: SyncSummary,
    report: ProgressReporter,
) -> None:
    """持久化一次源查询结果，并累计本轮同步统计。

    只有未被标题规则过滤且首次初始化工作流的公告，才会进入 new_refs。
    """
    summary.fetched_count += len(result.response.announcements)
    for announcement in result.items:
        if not announcement.announcement_id:
            summary.skipped_count += 1
            continue
        if repo.upsert_announcement(announcement):
            summary.inserted_announcements += 1
        else:
            summary.updated_announcements += 1
        decision = decide_title_filter(
            announcement.announcement_title,
            task.title_exclude_keywords,
        )
        hit_result = repo.upsert_hit(
            task=task,
            announcement=announcement,
            decision=decision,
        )
        if hit_result.inserted:
            summary.inserted_hits += 1
        else:
            summary.updated_hits += 1
        if hit_result.filter_status == "filtered":
            summary.filtered_hits += 1
            report(
                log_event(
                    "sync",
                    "filtered",
                    level="DEBUG",
                    **_announcement_log_fields(task, announcement),
                )
            )
            continue
        if repo.ensure_workflow_rows(
            hit_id=hit_result.hit_id,
            task=task,
            announcement=announcement,
        ):
            source = normalize_announcement_source(announcement.source)
            summary.seeded_summaries += 1
            summary.new_refs.append(
                AnnouncementRef(
                    source=source,
                    announcement_id=announcement.announcement_id,
                )
            )
            report(
                log_event(
                    "sync",
                    "seeded",
                    **_announcement_log_fields(task, announcement),
                )
            )


def _announcement_log_fields(task: SearchTask, announcement) -> dict[str, str]:
    return {
        "source": normalize_announcement_source(announcement.source),
        "stock": task.stock_code,
        "company": announcement.sec_name or _task_company_name(task) or task.stock_code,
        "keyword": task.search_keyword or "-",
        "ann_id": announcement.announcement_id,
        "title": announcement.announcement_title,
    }


def _task_company_name(task: SearchTask) -> str | None:
    stock_snapshot = task.config_snapshot.get("stock")
    if not isinstance(stock_snapshot, dict):
        return None
    name = stock_snapshot.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _group_tasks_by_source(
    tasks: Sequence[SearchTask],
) -> dict[AnnouncementSource, list[SearchTask]]:
    """按公告源分组，复用同一个源客户端处理多个任务。"""
    grouped: dict[AnnouncementSource, list[SearchTask]] = {}
    for task in tasks:
        grouped.setdefault(task.announcement_source, []).append(task)
    return grouped
