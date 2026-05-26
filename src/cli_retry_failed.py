from __future__ import annotations

from pathlib import Path

import typer

from cli_common import (
    report_pipeline_result,
    report_stage_result,
    setup_command_logging,
)

retry_failed_app = typer.Typer(
    help="Retry failed workflow stages",
    context_settings={"help_option_names": ["-h", "--help"]},
)


@retry_failed_app.command("summary")
def retry_failed_summary_command(
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    limit: int | None = typer.Option(None, help="Limit retry count"),
    log_level: str = typer.Option("INFO", help="Console and file log level"),
    log_dir: Path = typer.Option(Path("logs/runs"), help="Per-run log directory"),
    no_log_file: bool = typer.Option(False, "--no-log-file", help="Disable log file"),
) -> None:
    from workflow.pending import retry_failed_summaries

    context = setup_command_logging(
        command="retry-failed-summary",
        env_file=env_file,
        limit=limit,
        log_level=log_level,
        log_dir=log_dir,
        no_log_file=no_log_file,
    )
    try:
        result = retry_failed_summaries(
            env_file=env_file,
            limit=limit,
            progress=context.report,
        )
        context.mark_stage_result("summary", result)
        report_stage_result(context.report, "retry-failed", "summary", result)
    except BaseException as exc:
        context.mark_failed(exc)
        raise
    finally:
        context.notify_finished()


@retry_failed_app.command("delivery")
def retry_failed_delivery_command(
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    config_file: Path | None = typer.Option(None, help="Specify watchlist YAML"),
    limit: int | None = typer.Option(None, help="Limit retry count"),
    log_level: str = typer.Option("INFO", help="Console and file log level"),
    log_dir: Path = typer.Option(Path("logs/runs"), help="Per-run log directory"),
    no_log_file: bool = typer.Option(False, "--no-log-file", help="Disable log file"),
) -> None:
    from workflow.pending import retry_failed_deliveries

    context = setup_command_logging(
        command="retry-failed-delivery",
        env_file=env_file,
        config_file=config_file,
        limit=limit,
        log_level=log_level,
        log_dir=log_dir,
        no_log_file=no_log_file,
    )
    try:
        result = retry_failed_deliveries(
            env_file=env_file,
            config_file=config_file,
            limit=limit,
            progress=context.report,
        )
        context.mark_stage_result("delivery", result)
        report_stage_result(context.report, "retry-failed", "delivery", result)
    except BaseException as exc:
        context.mark_failed(exc)
        raise
    finally:
        context.notify_finished()


@retry_failed_app.command("all")
def retry_failed_all_command(
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    config_file: Path | None = typer.Option(None, help="Specify watchlist YAML"),
    limit: int | None = typer.Option(None, help="Limit retry count"),
    log_level: str = typer.Option("INFO", help="Console and file log level"),
    log_dir: Path = typer.Option(Path("logs/runs"), help="Per-run log directory"),
    no_log_file: bool = typer.Option(False, "--no-log-file", help="Disable log file"),
) -> None:
    from workflow.pending import retry_failed_all

    context = setup_command_logging(
        command="retry-failed-all",
        env_file=env_file,
        config_file=config_file,
        limit=limit,
        log_level=log_level,
        log_dir=log_dir,
        no_log_file=no_log_file,
    )
    try:
        summary_result, delivery_result = retry_failed_all(
            env_file=env_file,
            config_file=config_file,
            limit=limit,
            progress=context.report,
        )
        context.mark_pipeline_result(summary_result, delivery_result)
        report_pipeline_result(
            context.report,
            "retry-failed",
            summary_result,
            delivery_result,
        )
    except BaseException as exc:
        context.mark_failed(exc)
        raise
    finally:
        context.notify_finished()
