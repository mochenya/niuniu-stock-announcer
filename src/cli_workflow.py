from __future__ import annotations

from pathlib import Path

import typer

from cli_common import (
    report_pipeline_result,
    report_sync_finished,
    setup_command_logging,
)
from config.runtime import load_runtime_config
from db.connection import connect_database
from db.schema import ensure_schema, reset_schema
from log.events import log_event


def register_workflow_commands(app: typer.Typer) -> None:
    app.command("init-db")(init_database)
    app.command("sync")(sync)
    app.command("run")(run)
    app.command("process-pending")(process_pending_command)


def init_database(
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    reset: bool = typer.Option(False, "--reset", help="Drop workflow tables first"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm destructive reset"),
    log_level: str = typer.Option("INFO", help="Console and file log level"),
    log_dir: Path = typer.Option(Path("logs/runs"), help="Per-run log directory"),
    no_log_file: bool = typer.Option(False, "--no-log-file", help="Disable log file"),
) -> None:
    report = setup_command_logging(
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
    from workflow.common import require_database_url

    runtime_config = load_runtime_config(env_file=env_file, require_database=True)
    with connect_database(require_database_url(runtime_config)) as conn:
        if reset:
            reset_schema(conn)
        else:
            ensure_schema(conn)
        conn.commit()
    report(log_event("db", "schema_ready", reset=reset))


def sync(
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    config_file: Path | None = typer.Option(None, help="Specify watchlist YAML"),
    window_days: int | None = typer.Option(None, help="Override sync window days"),
    log_level: str = typer.Option("INFO", help="Console and file log level"),
    log_dir: Path = typer.Option(Path("logs/runs"), help="Per-run log directory"),
    no_log_file: bool = typer.Option(False, "--no-log-file", help="Disable log file"),
) -> None:
    from workflow.sync import sync_once

    report = setup_command_logging(
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
    report_sync_finished(report, summary)


def run(
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    config_file: Path | None = typer.Option(None, help="Specify watchlist YAML"),
    window_days: int | None = typer.Option(None, help="Override sync window days"),
    limit: int | None = typer.Option(None, help="Limit newly seeded summaries"),
    log_level: str = typer.Option("INFO", help="Console and file log level"),
    log_dir: Path = typer.Option(Path("logs/runs"), help="Per-run log directory"),
    no_log_file: bool = typer.Option(False, "--no-log-file", help="Disable log file"),
) -> None:
    from workflow.pending import run_new_workflow
    from workflow.sync import sync_once

    report = setup_command_logging(
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
    report_pipeline_result(report, "run", summary_result, delivery_result)


def process_pending_command(
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    limit: int | None = typer.Option(None, help="Limit pending rows"),
    log_level: str = typer.Option("INFO", help="Console and file log level"),
    log_dir: Path = typer.Option(Path("logs/runs"), help="Per-run log directory"),
    no_log_file: bool = typer.Option(False, "--no-log-file", help="Disable log file"),
) -> None:
    from workflow.pending import process_pending

    report = setup_command_logging(
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
    report_pipeline_result(report, "process-pending", summary_result, delivery_result)
