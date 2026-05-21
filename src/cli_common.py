from __future__ import annotations

from pathlib import Path

from log.config import setup_cli_logging
from log.events import log_event
from log.reporter import build_progress_reporter


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
    report(
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
    return report


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
