from __future__ import annotations

from collections.abc import Callable

from loguru import logger

from log.events import LogEvent


def build_progress_reporter() -> Callable[[LogEvent | str], None]:
    """把 workflow 的 progress 回调适配为 Loguru 输出。

    这里保留 str 兼容，是为了后续渐进迁移旧 workflow 输出或第三方回调时，
    不要求所有调用点一次性改成 LogEvent。
    """

    def report(event: LogEvent | str) -> None:
        if isinstance(event, LogEvent):
            _emit_event(event)
            return
        logger.bind(
            stage="workflow",
            event="progress",
            progress="",
            fields=None,
        ).info(event)

    return report


def _emit_event(event: LogEvent) -> None:
    """通过 bind 写入固定维度，真正的排版交给 config.py 中的 sink format。"""
    logger.bind(
        stage=event.stage,
        event=event.event,
        progress=event.progress or "",
        fields=event.fields,
    ).log(event.level.upper(), "")
