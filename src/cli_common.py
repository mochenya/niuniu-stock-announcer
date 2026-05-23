from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

from loguru import logger

from delivery.telegram.run_log import (
    RunLogNotification,
    RunLogStageStats,
    RunLogSyncStats,
    send_run_log_notification,
)
from log.config import setup_cli_logging
from log.events import log_event
from log.reporter import build_progress_reporter

SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(slots=True)
class CommandRunContext:
    command: str
    env_file: Path | None
    log_file: Path | None
    started_at: datetime
    started_monotonic: float
    report: Callable
    sync: RunLogSyncStats | None = None
    summary: RunLogStageStats | None = None
    delivery: RunLogStageStats | None = None
    error: str | None = None

    def __call__(self, event) -> None:
        self.report(event)

    def mark_sync_result(self, summary, *, include_new_refs: bool = False) -> None:
        self.sync = RunLogSyncStats(
            fetched=summary.fetched_count,
            filtered=summary.filtered_hits,
            seeded=summary.seeded_summaries,
            errors=len(summary.errors),
            new_refs=len(summary.new_refs) if include_new_refs else None,
        )

    def mark_stage_result(self, stage: str, result) -> None:
        stats = RunLogStageStats(
            completed=result.completed_count,
            failed=result.failed_count,
            unknown=result.unknown_count,
        )
        if stage == "summary":
            self.summary = stats
        elif stage == "delivery":
            self.delivery = stats
        else:
            raise ValueError(f"unsupported run log stage: {stage}")

    def mark_pipeline_result(self, summary_result, delivery_result) -> None:
        self.mark_stage_result("summary", summary_result)
        self.mark_stage_result("delivery", delivery_result)

    def mark_failed(self, exc: BaseException) -> None:
        self.error = str(exc).strip() or exc.__class__.__name__

    def notify_finished(self) -> None:
        notification = RunLogNotification(
            command=_format_cli_command(self.command),
            status=self._status(),
            started_at=self.started_at,
            finished_at=datetime.now(SHANGHAI_TIMEZONE),
            duration_seconds=perf_counter() - self.started_monotonic,
            log_file=self.log_file,
            sync=self.sync,
            summary=self.summary,
            delivery=self.delivery,
            error=self.error,
        )
        try:
            send_run_log_notification(notification, env_file=self.env_file)
        except Exception as exc:
            logger.bind(
                stage="telegram",
                event="run_log_failed",
                progress="",
                fields={"command": self.command, "error": str(exc)},
            ).warning("")

    def _status(self) -> str:
        if self.error:
            return "failed"
        if self.sync is not None and self.sync.errors:
            return "warning"
        if self.summary is not None and (self.summary.failed or self.summary.unknown):
            return "warning"
        if self.delivery is not None and (
            self.delivery.failed or self.delivery.unknown
        ):
            return "warning"
        return "success"


def setup_command_logging(
    *,
    command: str,
    env_file: Path | None,
    config_file: Path | None = None,
    window_days: int | None = None,
    limit: int | None = None,
    log_level: str,
    log_dir: Path,
    no_log_file: bool,
):
    """所有 CLI 命令统一从这里初始化日志，避免不同命令输出格式漂移。"""
    handle = setup_cli_logging(
        command=command,
        level=log_level,
        log_dir=log_dir,
        enable_file=not no_log_file,
    )
    report = build_progress_reporter()
    context = CommandRunContext(
        command=command,
        env_file=env_file,
        log_file=handle.log_file,
        started_at=datetime.now(SHANGHAI_TIMEZONE),
        started_monotonic=perf_counter(),
        report=report,
    )
    context.report(
        log_event(
            "cli",
            "started",
            command=command,
            log_file=handle.log_file,
            env=env_file or ".env",
            config=config_file,
            window_days=window_days,
            limit=limit,
        )
    )
    return context


def report_sync_finished(report, summary) -> None:
    report(
        log_event(
            "sync",
            "result",
            fetched=summary.fetched_count,
            filtered=summary.filtered_hits,
            seeded=summary.seeded_summaries,
            errors=len(summary.errors),
        )
    )


def _format_cli_command(command: str) -> str:
    if command.startswith("retry-failed-"):
        retry_target = command.removeprefix("retry-failed-")
        return f"uv run niuniu-stock retry-failed {retry_target}"
    return f"uv run niuniu-stock {command}"


def report_stage_result(report, stage: str, event: str, result) -> None:
    report(
        log_event(
            stage,
            event,
            completed=result.completed_count,
            failed=result.failed_count,
            unknown=result.unknown_count,
        )
    )


def report_pipeline_result(report, stage: str, summary_result, delivery_result) -> None:
    report(
        log_event(
            stage,
            "finished",
            summary_ok=summary_result.completed_count,
            summary_failed=summary_result.failed_count,
            delivery_ok=delivery_result.completed_count,
            delivery_failed=delivery_result.failed_count,
            delivery_unknown=delivery_result.unknown_count,
        )
    )
