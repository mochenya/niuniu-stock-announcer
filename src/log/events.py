from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias

LogFieldValue: TypeAlias = str | int | float | bool | Path | None


@dataclass(frozen=True, slots=True)
class LogEvent:
    """workflow 到 CLI 的日志事件契约。

    stage/event 是稳定的机器可读维度；fields 是业务上下文，例如公告 ID、
    股票代码和公司名。显示格式不放在业务层，方便以后增加 JSON 或文件 sink。
    """

    stage: str
    event: str
    level: str = "INFO"
    progress: str | None = None
    fields: dict[str, LogFieldValue] = field(default_factory=dict)


def log_event(
    stage: str,
    event: str,
    *,
    level: str = "INFO",
    progress: str | None = None,
    **fields: LogFieldValue,
) -> LogEvent:
    """构造一条结构化日志事件，具体怎么展示由 CLI reporter 决定。"""
    return LogEvent(
        stage=stage,
        event=event,
        level=level,
        progress=progress,
        fields=fields,
    )
