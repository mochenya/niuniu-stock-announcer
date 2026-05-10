from __future__ import annotations

import shutil
import unicodedata
from pathlib import Path
from typing import Any

# 字段顺序固定后，终端日志扫起来稳定；新增字段会自动排在最后，不影响旧格式。
FIELD_ORDER = [
    "command",
    "log_file",
    "env",
    "config",
    "window",
    "window_days",
    "mode",
    "limit",
    "tasks",
    "source",
    "stock",
    "company",
    "keyword",
    "target",
    "ann_id",
    "title",
    "fetched",
    "selected",
    "filtered",
    "seeded",
    "new_refs",
    "errors",
    "completed",
    "failed",
    "unknown",
    "summary_ok",
    "summary_failed",
    "delivery_ok",
    "delivery_failed",
    "delivery_unknown",
    "tokens",
    "pdf",
    "error",
]
TITLE_MAX_WIDTH = 48
CONTEXT_MIN_WIDTH = 48
CONTEXT_PREFIX_WIDTH = 60


def render_log_record(record: dict, *, truncate: bool) -> str:
    """把 Loguru record 渲染成终端友好的一行正文。"""
    extra = record["extra"]
    extra.setdefault("stage", "cli")
    extra.setdefault("event", "message")

    progress = str(extra.get("progress") or "")
    fields = extra.get("fields")
    if isinstance(fields, dict):
        context = format_fields(fields, truncate=truncate)
    else:
        context = str(record["message"]).strip()

    progress_column = _clip_display_width(progress, 7, ellipsis="")
    if context:
        return f"{progress_column:<7} | {context}"
    return f"{progress_column:<7} | -"


def format_fields(fields: dict[str, Any], *, truncate: bool) -> str:
    """把结构化字段转成 key=value；控制台模式只截断长标题。"""
    if not fields:
        return ""

    normalized = {
        key: _format_value(value)
        for key, value in fields.items()
        if value is not None and _format_value(value) != ""
    }
    if truncate and "title" in normalized:
        # 公告标题经常很长；控制台截断，文件日志通过 truncate=False 保留完整值。
        normalized["title"] = _clip_display_width(
            normalized["title"],
            min(TITLE_MAX_WIDTH, _context_width()),
        )

    ordered_keys = [key for key in FIELD_ORDER if key in normalized]
    ordered_keys.extend(key for key in normalized if key not in set(ordered_keys))
    return " ".join(f"{key}={normalized[key]}" for key in ordered_keys)


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Path):
        return _format_path(value)
    if isinstance(value, float):
        return f"{value:g}"
    return " ".join(str(value).split())


def _format_path(path: Path) -> str:
    """项目内路径尽量显示为相对路径，日志更短也更容易复制。"""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _context_width() -> int:
    """按当前终端宽度估算正文空间，给 stage/event/progress 预留固定列。"""
    terminal_width = shutil.get_terminal_size((120, 20)).columns
    return max(CONTEXT_MIN_WIDTH, terminal_width - CONTEXT_PREFIX_WIDTH)


def _clip_display_width(text: str, max_width: int, *, ellipsis: str = "...") -> str:
    """按显示宽度裁剪，避免中文标题在终端里突破列宽。"""
    if _display_width(text) <= max_width:
        return text
    budget = max_width - _display_width(ellipsis)
    if budget <= 0:
        return ellipsis[:max_width]

    used = 0
    chars: list[str] = []
    for char in text:
        char_width = _char_width(char)
        if used + char_width > budget:
            break
        chars.append(char)
        used += char_width
    return "".join(chars).rstrip() + ellipsis


def _display_width(text: str) -> int:
    return sum(_char_width(char) for char in text)


def _char_width(char: str) -> int:
    """轻量估算 East Asian Width，避免为日志格式额外引入宽字符依赖。"""
    if unicodedata.combining(char):
        return 0
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return 2
    return 1
