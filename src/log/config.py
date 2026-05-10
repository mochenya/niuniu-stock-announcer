from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger

from config.paths import PROJECT_ROOT
from log.formatters import render_log_record


@dataclass(frozen=True, slots=True)
class CliLoggingHandle:
    """返回给 CLI 的日志运行上下文，便于启动日志展示文件位置。"""

    log_file: Path | None


def setup_cli_logging(
    *,
    command: str,
    level: str = "INFO",
    log_dir: str | Path = Path("logs/runs"),
    enable_file: bool = True,
) -> CliLoggingHandle:
    """配置 CLI 控制台日志和每次运行的日志文件。

    控制台日志服务于人工实时查看，默认截断长标题；文件日志用于事后排查，
    保留完整字段。两者共用同一套结构化事件，避免 CLI 和 workflow 各自拼格式。
    """
    normalized_level = level.upper()
    # CLI 每次启动都会重新配置 sink，避免 Typer 子命令或测试复用时重复输出。
    logger.remove()
    logger.add(
        sys.stderr,
        level=normalized_level,
        colorize=sys.stderr.isatty(),
        format=_console_format,
    )

    log_file: Path | None = None
    if enable_file:
        resolved_log_dir = _resolve_log_dir(log_dir)
        resolved_log_dir.mkdir(parents=True, exist_ok=True)
        log_file = resolved_log_dir / _build_log_filename(command)
        logger.add(
            log_file,
            level=normalized_level,
            encoding="utf-8",
            enqueue=False,
            format=_file_format,
        )
    return CliLoggingHandle(log_file=log_file)


def _console_format(record: dict) -> str:
    # 控制台只展示 HH:mm:ss 和截断后的正文，保证窄终端下仍然是一行一条。
    record["extra"]["rendered"] = render_log_record(record, truncate=True)
    return (
        "<green>{time:HH:mm:ss.SSS}</green> | "
        "<level>{level: <7}</level> | "
        "<cyan>{extra[stage]: <8}</cyan> | "
        "<cyan>{extra[event]: <14}</cyan> | "
        "{extra[rendered]}\n{exception}"
    )


def _file_format(record: dict) -> str:
    # 文件日志保留完整日期和完整字段，后续排查公告标题、PDF 路径时不能丢信息。
    record["extra"]["rendered"] = render_log_record(record, truncate=False)
    return (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
        "{level: <7} | "
        "{extra[stage]: <8} | "
        "{extra[event]: <14} | "
        "{extra[rendered]}\n{exception}"
    )


def _resolve_log_dir(log_dir: str | Path) -> Path:
    """相对日志目录按项目根目录解析，避免从不同 cwd 启动时日志散落。"""
    resolved = Path(log_dir)
    if resolved.is_absolute():
        return resolved
    return (PROJECT_ROOT / resolved).resolve()


def _build_log_filename(command: str) -> str:
    """用命令名区分每次运行的日志，文件名只保留跨平台安全字符。"""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_command = re.sub(r"[^a-zA-Z0-9_.-]+", "-", command.strip() or "cli").strip("-")
    return f"{timestamp}-{safe_command or 'cli'}.log"
