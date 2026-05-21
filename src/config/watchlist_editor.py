from __future__ import annotations

from contextlib import suppress
from io import StringIO
from pathlib import Path
from stat import S_IMODE
from tempfile import NamedTemporaryFile
from typing import Any, get_args

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
import yaml

from config.runtime import load_runtime_config
from domain.common import Market, normalize_required_text
from domain.config_models import WatchlistConfig

ALLOWED_MARKETS = tuple(get_args(Market))
ROUNDTRIP_YAML = YAML()
ROUNDTRIP_YAML.preserve_quotes = True
ROUNDTRIP_YAML.indent(mapping=2, sequence=4, offset=2)


class WatchlistEditError(ValueError):
    pass


def resolve_watchlist_edit_path(
    *,
    env_file: str | Path | None,
    config_file: str | Path | None,
) -> Path:
    if config_file is not None:
        path = Path(config_file).expanduser()
        return path if path.is_absolute() else path.resolve()
    return load_runtime_config(env_file=env_file).watchlist_file


def load_watchlist_payload(config_path: str | Path) -> dict[str, Any]:
    resolved_path = Path(config_path)
    if not resolved_path.exists():
        raise WatchlistEditError(f"Config file not found: {resolved_path}")
    try:
        content = resolved_path.read_text(encoding="utf-8")
    except OSError as error:
        raise WatchlistEditError(f"Cannot read config file: {error}") from error
    try:
        payload = ROUNDTRIP_YAML.load(content)
    except YAMLError as error:
        raise WatchlistEditError(f"Invalid YAML: {error}") from error
    if not isinstance(payload, dict):
        raise WatchlistEditError("Watchlist config must be a YAML object.")
    return payload


def add_stock_to_payload(
    payload: dict[str, Any],
    *,
    market: str,
    code: str,
) -> bool:
    normalized_market = _normalize_market(market)
    normalized_code = _normalize_required_text(code, field_name="code")
    stocks = _ensure_list(payload, "stocks")
    for stock in stocks:
        if not isinstance(stock, dict):
            continue
        stock_code = str(stock.get("code", ""))
        if stock.get("market") == normalized_market and stock_code == normalized_code:
            return False
    stock = {
        "market": normalized_market,
        "code": DoubleQuotedScalarString(normalized_code),
        "keywords": [],
        "exclude_keywords": [],
    }
    _append_list_item(stocks, stock, blank_before=bool(stocks))
    return True


def add_global_keyword_to_payload(payload: dict[str, Any], *, keyword: str) -> bool:
    normalized_keyword = _normalize_required_text(keyword, field_name="keyword")
    filters = payload.setdefault("filters", {})
    if not isinstance(filters, dict):
        raise WatchlistEditError("filters must be a YAML object.")
    keywords = filters.setdefault("title_exclude_keywords", [])
    if not isinstance(keywords, list):
        raise WatchlistEditError("filters.title_exclude_keywords must be a list.")
    normalized_existing = [
        _normalize_required_text(item, field_name="title_exclude_keywords")
        for item in keywords
    ]
    if normalized_keyword in normalized_existing:
        return False
    _append_list_item(keywords, normalized_keyword)
    return True


def _append_list_item(
    items: list[Any],
    value: Any,
    *,
    blank_before: bool = False,
) -> None:
    trailing_comment = _pop_blank_trailing_comment(items)
    items.append(value)
    if blank_before and hasattr(items, "yaml_set_comment_before_after_key"):
        items.yaml_set_comment_before_after_key(len(items) - 1, before="")
    if trailing_comment is not None:
        items.ca.items[len(items) - 1] = trailing_comment


def _pop_blank_trailing_comment(items: list[Any]) -> object | None:
    if not items or not hasattr(items, "ca"):
        return None
    comments = items.ca.items
    trailing_comment = comments.get(len(items) - 1)
    if not trailing_comment:
        return None
    first_token = trailing_comment[0]
    if getattr(first_token, "value", "").strip():
        return None
    return comments.pop(len(items) - 1)


def save_watchlist_payload(config_path: str | Path, payload: dict[str, Any]) -> None:
    validate_watchlist_payload(payload)
    text = dump_watchlist_payload(payload)
    roundtrip_payload = yaml.safe_load(text)
    if not isinstance(roundtrip_payload, dict):
        raise WatchlistEditError("Serialized watchlist config must be a YAML object.")
    validate_watchlist_payload(roundtrip_payload)
    _write_text_atomic(Path(config_path), text)


def dump_watchlist_payload(payload: dict[str, Any]) -> str:
    stream = StringIO()
    ROUNDTRIP_YAML.dump(payload, stream)
    return stream.getvalue()


def validate_watchlist_payload(payload: dict[str, Any]) -> WatchlistConfig:
    try:
        return WatchlistConfig.model_validate(payload)
    except ValidationError as error:
        raise WatchlistEditError(str(error)) from error


def _ensure_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.setdefault(key, [])
    if not isinstance(value, list):
        raise WatchlistEditError(f"{key} must be a list.")
    return value


def _write_text_atomic(config_path: Path, text: str) -> None:
    temp_path: Path | None = None
    try:
        original_mode = (
            S_IMODE(config_path.stat().st_mode) if config_path.exists() else None
        )
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(text)
            temp_path = Path(temp_file.name)
        temp_path.replace(config_path)
        if original_mode is not None:
            config_path.chmod(original_mode)
    except OSError as error:
        if temp_path is not None:
            with suppress(OSError):
                temp_path.unlink(missing_ok=True)
        raise WatchlistEditError(f"Cannot write config file: {error}") from error


def _normalize_required_text(value: object, *, field_name: str) -> str:
    try:
        return normalize_required_text(value, field_name=field_name)
    except (TypeError, ValueError) as error:
        raise WatchlistEditError(str(error)) from error


def _normalize_market(market: str) -> str:
    normalized_market = _normalize_required_text(market, field_name="market").lower()
    if normalized_market not in ALLOWED_MARKETS:
        expected = ", ".join(ALLOWED_MARKETS)
        raise WatchlistEditError(
            f"Invalid market: {normalized_market}. Expected one of: {expected}."
        )
    return normalized_market
