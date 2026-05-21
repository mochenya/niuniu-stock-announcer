from __future__ import annotations

from pathlib import Path

import typer

from config.watchlist_editor import (
    WatchlistEditError,
    add_global_keyword_to_payload,
    add_stock_to_payload,
    load_watchlist_payload,
    resolve_watchlist_edit_path,
    save_watchlist_payload,
    validate_watchlist_payload,
)
from config.watchlist_verifier import (
    StockAnnouncementCheckError,
    verify_stock_has_recent_announcements,
)

config_app = typer.Typer(
    help="Edit watchlist YAML config",
    context_settings={"help_option_names": ["-h", "--help"]},
)
config_add_app = typer.Typer(
    help="Add entries to watchlist YAML config",
    context_settings={"help_option_names": ["-h", "--help"]},
)
config_app.add_typer(config_add_app, name="add")


@config_app.callback()
def config_callback(
    ctx: typer.Context,
    env_file: Path | None = typer.Option(None, help="Specify .env file"),
    config_file: Path | None = typer.Option(None, help="Specify watchlist YAML"),
) -> None:
    try:
        ctx.obj = {
            "config_file": resolve_watchlist_edit_path(
                env_file=env_file,
                config_file=config_file,
            )
        }
    except Exception as error:
        _fail_config_command(error)


@config_add_app.command("stock")
def add_config_stock(
    ctx: typer.Context,
    market: str = typer.Argument(..., help="Market: sh, sz, bj, hk"),
    code: str = typer.Argument(..., help="Stock code"),
) -> None:
    config_path = _get_config_file_from_context(ctx)
    try:
        payload = load_watchlist_payload(config_path)
        added = add_stock_to_payload(payload, market=market, code=code)
        if not added:
            stock_key = f"{market.strip().lower()}:{code.strip()}"
            _print_config_result(
                f"Stock already exists: {stock_key}",
                config_path,
                typer.colors.YELLOW,
            )
            return
        watchlist_config = validate_watchlist_payload(payload)
        added_stock = watchlist_config.stocks[-1]
        source = watchlist_config.sources.source_for_market(added_stock.market)
        check_result = verify_stock_has_recent_announcements(
            market=added_stock.market,
            code=added_stock.code,
            source=source,
        )
        _print_stock_check_result(check_result)
        if not check_result.found:
            typer.secho(
                "No announcements found in the last 60 days; adding stock anyway.",
                fg=typer.colors.YELLOW,
            )
        save_watchlist_payload(config_path, payload)
        _print_config_result(
            f"Added stock: {check_result.stock_key}",
            config_path,
            typer.colors.GREEN,
        )
    except StockAnnouncementCheckError as error:
        _fail_stock_check(error, config_path)
    except (WatchlistEditError, ValueError) as error:
        _fail_config_command(error)


@config_add_app.command("global-keyword")
def add_config_global_keyword(
    ctx: typer.Context,
    keyword: str = typer.Argument(..., help="Global title exclude keyword"),
) -> None:
    config_path = _get_config_file_from_context(ctx)
    try:
        payload = load_watchlist_payload(config_path)
        added = add_global_keyword_to_payload(payload, keyword=keyword)
        if added:
            save_watchlist_payload(config_path, payload)
            _print_config_result(
                f"Added global keyword: {keyword.strip()}",
                config_path,
                typer.colors.GREEN,
            )
            return
        _print_config_result(
            f"Global keyword already exists: {keyword.strip()}",
            config_path,
            typer.colors.YELLOW,
        )
    except WatchlistEditError as error:
        _fail_config_command(error)


def _get_config_file_from_context(ctx: typer.Context) -> Path:
    if isinstance(ctx.obj, dict) and isinstance(ctx.obj.get("config_file"), Path):
        return ctx.obj["config_file"]
    _fail_config_command("Cannot resolve watchlist config file.")


def _print_config_result(message: str, config_path: Path, color: str) -> None:
    typer.secho(message, fg=color)
    typer.echo(f"Config file: {config_path}")


def _print_stock_check_result(result) -> None:
    typer.echo(f"Checking announcements: {result.stock_key}")
    typer.echo(f"Source: {result.source}")
    typer.echo(
        f"Window: {result.start_date.isoformat()}..{result.end_date.isoformat()}"
    )
    typer.echo()
    if not result.found:
        return
    typer.secho(f"Found {result.found_count} announcements.", fg=typer.colors.GREEN)
    if result.preview_items:
        typer.echo("Latest:")
        for item in result.preview_items:
            typer.echo(f"  {item.announcement_date}  {item.title}")
    typer.echo()


def _fail_stock_check(error: object, config_path: Path) -> None:
    typer.secho(f"Announcement check failed: {error}", fg=typer.colors.RED, err=True)
    typer.echo(f"Config file unchanged: {config_path}")
    raise typer.Exit(code=1)


def _fail_config_command(error: object) -> None:
    typer.secho(str(error), fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)
