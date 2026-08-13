"""固定 v2 Typer 命令面。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Callable, TypeVar

import typer

from niuniu_stock_announcer.bootstrap import (
    ApplicationContext,
    RecoveryResult,
    bootstrap,
)
from niuniu_stock_announcer.config.plan_loader import PlanLoadError, load_china_plan
from niuniu_stock_announcer.config.settings import load_app_settings

T = TypeVar("T")

app = typer.Typer(
    help="牛牛股票公告员 v2 命令行。",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
plan_app = typer.Typer(
    help="校验 China Plan。",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
retry_app = typer.Typer(
    help="显式重试确定失败的任务。",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
db_app = typer.Typer(
    help="查看或升级 v2 数据库。",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(plan_app, name="plan")
app.add_typer(retry_app, name="retry-failed")
app.add_typer(db_app, name="db")


@plan_app.command("validate")
def plan_validate(
    plan: Annotated[
        list[Path] | None,
        typer.Option("--plan", help="唯一的 China Plan YAML 文件。"),
    ] = None,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="可选的环境文件。"),
    ] = None,
) -> None:
    """校验一份 Plan，不连接数据库或外部服务。"""
    try:
        plan_path = _require_one_plan(plan)
        loaded = load_china_plan(plan_path, env_file=env_file)
    except (PlanLoadError, ValueError) as exc:
        _fail(exc)
    typer.echo(f"Plan 校验通过：{loaded.plan_key} ({loaded.plan_type})")


@app.command("sync")
def sync(
    plan: Annotated[
        list[Path] | None,
        typer.Option("--plan", help="唯一的 China Plan YAML 文件。"),
    ] = None,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="可选的环境文件。"),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="每次查询的安全结果上限。"),
    ] = None,
) -> None:
    """执行单个 Plan 的公告 discovery。"""
    plan_model, context = _load_context(plan, env_file)
    try:
        with context.open_pipeline(plan_model, include_postprocess=False) as pipeline:
            result = pipeline.sync(limit=limit)
    except Exception as exc:
        _fail(exc)
    typer.echo(
        f"同步完成：queries={result.queries_succeeded} "
        f"persisted={result.persisted_items} selected={result.selected_matches} "
        f"errors={len(result.errors)}"
    )


@app.command("run")
def run(
    plan: Annotated[
        list[Path] | None,
        typer.Option("--plan", help="唯一的 China Plan YAML 文件。"),
    ] = None,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="可选的环境文件。"),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="本轮查询与后处理上限。"),
    ] = None,
) -> None:
    """同步单个 Plan，并处理本轮新 selected match 的后续工作。"""
    plan_model, context = _load_context(plan, env_file)
    try:
        with context.open_pipeline(plan_model) as pipeline:
            result = pipeline.run(limit=limit)
    except Exception as exc:
        _fail(exc)
    typer.echo(
        f"运行完成：sync_selected={result.sync.selected_matches} "
        f"summary_completed={result.summary.completed_count} "
        f"delivery_sent={result.delivery.sent_count} "
        f"delivery_unknown={result.delivery.unknown_count}"
    )


@app.command("process-pending")
def process_pending(
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="可选的环境文件。"),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="本轮最多处理的任务数。"),
    ] = None,
) -> None:
    """处理全局 pending 摘要和 Telegram child，不执行 discovery。"""
    result = _run_recovery(
        env_file,
        limit,
        lambda application: application.process_pending(limit=limit),
    )
    _echo_recovery("pending 处理完成", result)


@retry_app.command("summary")
def retry_summary(
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="可选的环境文件。"),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="本轮最多重试的任务数。"),
    ] = None,
) -> None:
    """只重试确定失败的摘要。"""
    result = _run_recovery(
        env_file,
        limit,
        lambda application: application.retry_failed_summary(limit=limit),
        require_delivery=False,
    )
    typer.echo(
        f"摘要重试完成：completed={result.completed_count} "
        f"failed={result.failed_count} skipped={result.skipped_count}"
    )


@retry_app.command("telegram")
def retry_telegram(
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="可选的环境文件。"),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="本轮最多重试的消息数。"),
    ] = None,
) -> None:
    """只重试确定失败的 Telegram child。"""
    result = _run_recovery(
        env_file,
        limit,
        lambda application: application.retry_failed_telegram(limit=limit),
        require_summary=False,
    )
    typer.echo(
        f"Telegram 重试完成：sent={result.sent_count} "
        f"failed={result.failed_count} unknown={result.unknown_count}"
    )


@retry_app.command("all")
def retry_all(
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="可选的环境文件。"),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="本轮最多处理的任务数。"),
    ] = None,
) -> None:
    """先重试摘要，再处理确定失败和新产生的 pending Telegram child。"""
    result = _run_recovery(
        env_file,
        limit,
        lambda application: application.retry_failed_all(limit=limit),
    )
    _echo_recovery("全部失败任务处理完成", result)


@db_app.command("upgrade")
def db_upgrade(
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="可选的环境文件。"),
    ] = None,
) -> None:
    """把显式 DATABASE_URL 升级到当前 migration head。"""
    context = _load_settings(env_file)
    try:
        context.upgrade_database()
    except Exception as exc:
        _fail(exc)
    typer.echo("数据库升级完成。")


@db_app.command("current")
def db_current(
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="可选的环境文件。"),
    ] = None,
) -> None:
    """读取当前数据库 migration revision。"""
    context = _load_settings(env_file)
    try:
        revision = context.current_database_revision()
    except Exception as exc:
        _fail(exc)
    typer.echo(revision or "数据库尚未执行 migration。")


def _load_context(
    plan: list[Path] | None,
    env_file: Path | None,
) -> tuple[object, ApplicationContext]:
    try:
        plan_model = load_china_plan(_require_one_plan(plan), env_file=env_file)
        return plan_model, _load_settings(env_file)
    except (PlanLoadError, ValueError) as exc:
        _fail(exc)
    raise AssertionError("unreachable context load")


def _load_settings(env_file: Path | None) -> ApplicationContext:
    try:
        return bootstrap(load_app_settings(env_file=env_file))
    except Exception as exc:
        _fail(exc)
    raise AssertionError("unreachable settings load")


def _run_recovery(
    env_file: Path | None,
    limit: int | None,
    operation: Callable[[object], T],
    *,
    require_summary: bool = True,
    require_delivery: bool = True,
) -> T:
    context = _load_settings(env_file)
    try:
        with context.open_recovery(
            require_summary=require_summary,
            require_delivery=require_delivery,
        ) as application:
            return operation(application)
    except Exception as exc:
        _fail(exc)
    raise AssertionError("unreachable recovery operation")


def _require_one_plan(values: list[Path] | None) -> Path:
    paths = [] if values is None else values
    if len(paths) != 1:
        raise ValueError("必须通过 --plan 恰好提供一个普通 YAML 文件")
    path = paths[0]
    if not path.is_file():
        raise ValueError(f"Plan 必须是可读的普通文件: {path}")
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("Plan 必须使用 .yaml 或 .yml 文件")
    return path


def _echo_recovery(prefix: str, result: RecoveryResult) -> None:
    typer.echo(
        f"{prefix}：summary_completed={result.summary.completed_count} "
        f"summary_failed={result.summary.failed_count} "
        f"telegram_sent={result.delivery.sent_count} "
        f"telegram_failed={result.delivery.failed_count} "
        f"telegram_unknown={result.delivery.unknown_count}"
    )


def _fail(error: Exception) -> None:
    typer.echo(f"错误：{error}", err=True)
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
