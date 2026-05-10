from __future__ import annotations

from pathlib import Path

import typer

from config.runtime import load_runtime_config
from db.connection import connect_database
from db.schema import ensure_schema, reset_schema
from log.config import setup_cli_logging
from log.events import log_event
from log.reporter import build_progress_reporter
from workflow.pending import (
    process_pending,
    retry_failed_all,
    retry_failed_deliveries,
    retry_failed_summaries,
    run_new_workflow,
)
from workflow.sync import (
    sync_once,
)
from workflow.common import require_database_url

app = typer.Typer(
    help="NiuNiu Stock Announcer CLI",
    context_settings={"help_option_names": ["-h", "--help"]},
)
retry_failed_app = typer.Typer(
    help="Retry failed workflow stages",
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(retry_failed_app, name="retry-failed")


@app.command("init-db")
def init_database(
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    reset: bool = typer.Option(False, "--reset", help="Drop workflow tables first"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm destructive reset"),
    log_level: str = typer.Option("INFO", help="Console and file log level"),
    log_dir: Path = typer.Option(Path("logs/runs"), help="Per-run log directory"),
    no_log_file: bool = typer.Option(False, "--no-log-file", help="Disable log file"),
) -> None:
    report = _setup_command_logging(
        command="init-db",
        env_file=env_file,
        log_level=log_level,
        log_dir=log_dir,
        no_log_file=no_log_file,
    )
    if reset and not yes:
        if not typer.confirm("This will drop workflow tables. Continue?"):
            report(log_event("cli", "cancelled", command="init-db"))
            raise typer.Exit(code=1)
    runtime_config = load_runtime_config(env_file=env_file, require_database=True)
    with connect_database(require_database_url(runtime_config)) as conn:
        if reset:
            reset_schema(conn)
        else:
            ensure_schema(conn)
        conn.commit()
    report(log_event("db", "schema_ready", reset=reset))


@app.command()
def sync(
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    config_file: Path | None = typer.Option(None, help="Specify watchlist YAML"),
    window_days: int | None = typer.Option(None, help="Override sync window days"),
    log_level: str = typer.Option("INFO", help="Console and file log level"),
    log_dir: Path = typer.Option(Path("logs/runs"), help="Per-run log directory"),
    no_log_file: bool = typer.Option(False, "--no-log-file", help="Disable log file"),
) -> None:
    report = _setup_command_logging(
        command="sync",
        env_file=env_file,
        config_file=config_file,
        window_days=window_days,
        log_level=log_level,
        log_dir=log_dir,
        no_log_file=no_log_file,
    )
    summary = sync_once(
        env_file=env_file,
        config_file=config_file,
        window_days=window_days,
        progress=report,
    )
    _report_sync_finished(report, summary)


@app.command()
def run(
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    config_file: Path | None = typer.Option(None, help="Specify watchlist YAML"),
    window_days: int | None = typer.Option(None, help="Override sync window days"),
    limit: int | None = typer.Option(None, help="Limit newly seeded summaries"),
    log_level: str = typer.Option("INFO", help="Console and file log level"),
    log_dir: Path = typer.Option(Path("logs/runs"), help="Per-run log directory"),
    no_log_file: bool = typer.Option(False, "--no-log-file", help="Disable log file"),
) -> None:
    report = _setup_command_logging(
        command="run",
        env_file=env_file,
        config_file=config_file,
        window_days=window_days,
        limit=limit,
        log_level=log_level,
        log_dir=log_dir,
        no_log_file=no_log_file,
    )
    sync_summary = sync_once(
        env_file=env_file,
        config_file=config_file,
        window_days=window_days,
        progress=report,
    )
    report(
        log_event(
            "run",
            "sync_finished",
            fetched=sync_summary.fetched_count,
            filtered=sync_summary.filtered_hits,
            seeded=sync_summary.seeded_summaries,
            new_refs=len(sync_summary.new_refs),
            errors=len(sync_summary.errors),
        )
    )
    if not sync_summary.new_refs:
        report(
            log_event(
                "run",
                "no_new_records",
                fetched=sync_summary.fetched_count,
                filtered=sync_summary.filtered_hits,
                seeded=sync_summary.seeded_summaries,
                errors=len(sync_summary.errors),
            )
        )
        return
    report(
        log_event(
            "run",
            "workflow",
            mode="new-only",
            new_refs=len(sync_summary.new_refs),
            limit=limit or "-",
        )
    )
    summary_result, delivery_result = run_new_workflow(
        refs=sync_summary.new_refs,
        env_file=env_file,
        limit=limit,
        progress=report,
    )
    _report_pipeline_result(report, "run", summary_result, delivery_result)


@app.command("process-pending")
def process_pending_command(
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    limit: int | None = typer.Option(None, help="Limit pending rows"),
    log_level: str = typer.Option("INFO", help="Console and file log level"),
    log_dir: Path = typer.Option(Path("logs/runs"), help="Per-run log directory"),
    no_log_file: bool = typer.Option(False, "--no-log-file", help="Disable log file"),
) -> None:
    report = _setup_command_logging(
        command="process-pending",
        env_file=env_file,
        limit=limit,
        log_level=log_level,
        log_dir=log_dir,
        no_log_file=no_log_file,
    )
    summary_result, delivery_result = process_pending(
        env_file=env_file,
        limit=limit,
        progress=report,
    )
    _report_pipeline_result(report, "process-pending", summary_result, delivery_result)


@retry_failed_app.command("summary")
def retry_failed_summary_command(
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    limit: int | None = typer.Option(None, help="Limit retry count"),
    log_level: str = typer.Option("INFO", help="Console and file log level"),
    log_dir: Path = typer.Option(Path("logs/runs"), help="Per-run log directory"),
    no_log_file: bool = typer.Option(False, "--no-log-file", help="Disable log file"),
) -> None:
    report = _setup_command_logging(
        command="retry-failed-summary",
        env_file=env_file,
        limit=limit,
        log_level=log_level,
        log_dir=log_dir,
        no_log_file=no_log_file,
    )
    result = retry_failed_summaries(
        env_file=env_file,
        limit=limit,
        progress=report,
    )
    _report_stage_result(report, "retry-failed", "summary", result)


@retry_failed_app.command("delivery")
def retry_failed_delivery_command(
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    limit: int | None = typer.Option(None, help="Limit retry count"),
    log_level: str = typer.Option("INFO", help="Console and file log level"),
    log_dir: Path = typer.Option(Path("logs/runs"), help="Per-run log directory"),
    no_log_file: bool = typer.Option(False, "--no-log-file", help="Disable log file"),
) -> None:
    report = _setup_command_logging(
        command="retry-failed-delivery",
        env_file=env_file,
        limit=limit,
        log_level=log_level,
        log_dir=log_dir,
        no_log_file=no_log_file,
    )
    result = retry_failed_deliveries(
        env_file=env_file,
        limit=limit,
        progress=report,
    )
    _report_stage_result(report, "retry-failed", "delivery", result)


@retry_failed_app.command("all")
def retry_failed_all_command(
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    limit: int | None = typer.Option(None, help="Limit retry count"),
    log_level: str = typer.Option("INFO", help="Console and file log level"),
    log_dir: Path = typer.Option(Path("logs/runs"), help="Per-run log directory"),
    no_log_file: bool = typer.Option(False, "--no-log-file", help="Disable log file"),
) -> None:
    report = _setup_command_logging(
        command="retry-failed-all",
        env_file=env_file,
        limit=limit,
        log_level=log_level,
        log_dir=log_dir,
        no_log_file=no_log_file,
    )
    summary_result, delivery_result = retry_failed_all(
        env_file=env_file,
        limit=limit,
        progress=report,
    )
    _report_pipeline_result(report, "retry-failed", summary_result, delivery_result)


def _setup_command_logging(
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


def _report_sync_finished(report, summary) -> None:
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


def _report_stage_result(report, stage: str, event: str, result) -> None:
    report(
        log_event(
            stage,
            event,
            completed=result.completed_count,
            failed=result.failed_count,
            unknown=result.unknown_count,
        )
    )


def _report_pipeline_result(
    report, stage: str, summary_result, delivery_result
) -> None:
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


if __name__ == "__main__":
    app()
