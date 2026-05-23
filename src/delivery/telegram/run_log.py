from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from telegram import Bot
from telegram.constants import ParseMode

from config.runtime import load_runtime_config
from delivery.telegram.sender import _build_timeout_kwargs, _run_async
from delivery.telegram.target import TELEGRAM_TOPIC_HOSTS, parse_telegram_topic_url
from domain.config_models import RuntimeConfig


@dataclass(frozen=True, slots=True)
class RunLogSyncStats:
    fetched: int
    filtered: int
    seeded: int
    errors: int
    new_refs: int | None = None


@dataclass(frozen=True, slots=True)
class RunLogStageStats:
    completed: int
    failed: int
    unknown: int = 0


@dataclass(frozen=True, slots=True)
class RunLogNotification:
    command: str
    status: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    log_file: Path | None = None
    sync: RunLogSyncStats | None = None
    summary: RunLogStageStats | None = None
    delivery: RunLogStageStats | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _RunLogTelegramTarget:
    chat_id: int | str
    message_thread_id: int | None = None


def send_run_log_notification(
    notification: RunLogNotification,
    *,
    env_file: str | Path | None = None,
    config: RuntimeConfig | None = None,
) -> bool:
    """按需发送 CLI 运行播报。

    这是运维通知，不参与公告投递状态机；调用方会把异常降级为 warning，
    避免 Telegram 临时故障影响原命令的退出结果。
    """
    resolved_config = config or load_runtime_config(env_file=env_file)
    settings = resolved_config.telegram_run_log
    if not settings.enabled:
        return False
    if not settings.bot_token:
        raise ValueError("missing Telegram run log config: TELEGRAM_RUN_LOG_BOT_TOKEN")
    if not settings.target:
        raise ValueError("missing Telegram run log config: TELEGRAM_RUN_LOG_TARGET")

    return _run_async(
        _async_send_run_log_notification(
            notification,
            bot_token=settings.bot_token,
            target=_parse_run_log_target(settings.target),
            timeout=resolved_config.telegram.timeout,
            attach_file=settings.attach_file,
        )
    )


async def _async_send_run_log_notification(
    notification: RunLogNotification,
    *,
    bot_token: str,
    target: _RunLogTelegramTarget,
    timeout: float,
    attach_file: bool,
) -> bool:
    async with Bot(token=bot_token) as bot:
        await bot.send_message(
            chat_id=target.chat_id,
            message_thread_id=target.message_thread_id,
            text=format_run_log_message(
                notification,
                will_attach_file=_should_attach_file(notification, attach_file),
            ),
            parse_mode=ParseMode.HTML,
            **_build_timeout_kwargs(timeout),
        )
        if _should_attach_file(notification, attach_file):
            assert notification.log_file is not None
            with notification.log_file.open("rb") as document_file:
                await bot.send_document(
                    chat_id=target.chat_id,
                    message_thread_id=target.message_thread_id,
                    document=document_file,
                    filename=notification.log_file.name,
                    caption=_format_log_file_caption(notification.log_file),
                    parse_mode=ParseMode.HTML,
                    **_build_timeout_kwargs(timeout),
                )
    return True


def format_run_log_message(
    notification: RunLogNotification,
    *,
    will_attach_file: bool,
) -> str:
    status_icon = _status_icon(notification.status)
    status_text = _status_text(notification.status)
    lines = [
        "<b>📣 牛牛股票公告员</b>",
        f"<code>{_escape(notification.command)}</code>",
        "",
        f"{status_icon} <b>运行状态：</b>{status_text}",
        f"⏱️ <b>运行耗时：</b>{_format_duration(notification.duration_seconds)}",
        f"🕘 <b>开始时间：</b>{_format_time(notification.started_at)}",
        f"🏁 <b>结束时间：</b>{_format_time(notification.finished_at)}",
    ]

    if notification.sync is not None:
        lines.extend(["", "<b>📥 同步结果</b>"])
        if notification.sync.new_refs is None:
            lines.extend(
                [
                    f"• 获取：<b>{notification.sync.fetched}</b>",
                    f"• 过滤：<b>{notification.sync.filtered}</b>",
                    f"• 新增：<b>{notification.sync.seeded}</b>",
                    f"• 错误：<b>{notification.sync.errors}</b>",
                ]
            )
        else:
            lines.extend(
                [
                    f"• 获取：<b>{notification.sync.fetched}</b>",
                    f"• 过滤：<b>{notification.sync.filtered}</b>",
                    f"• 入队：<b>{notification.sync.seeded}</b>",
                    f"• 本轮新增：<b>{notification.sync.new_refs}</b>",
                    f"• 错误：<b>{notification.sync.errors}</b>",
                ]
            )

    if notification.summary is not None:
        lines.extend(
            [
                "",
                "<b>🧠 摘要结果</b>",
                f"• 成功：<b>{notification.summary.completed}</b>",
                f"• 失败：<b>{notification.summary.failed}</b>",
            ]
        )

    if notification.delivery is not None:
        lines.extend(
            [
                "",
                "<b>📨 Telegram投递</b>",
                f"• 成功：<b>{notification.delivery.completed}</b>",
                f"• 失败：<b>{notification.delivery.failed}</b>",
                f"• 未知：<b>{notification.delivery.unknown}</b>",
            ]
        )

    if notification.error:
        lines.extend(
            [
                "",
                "<b>⚠️ 错误信息</b>",
                f"<code>{_escape(_compact_text(notification.error, max_length=900))}</code>",
            ]
        )

    if will_attach_file:
        lines.extend(["", "📎 <i>完整运行日志已作为附件发送</i>"])
    elif notification.log_file is not None:
        lines.extend(
            [
                "",
                f"🗂 <i>日志文件：</i><code>{_escape(str(notification.log_file))}</code>",
            ]
        )

    return "\n".join(lines)


def _parse_run_log_target(raw_target: str) -> _RunLogTelegramTarget:
    target = raw_target.strip()
    if target.startswith("@"):
        return _RunLogTelegramTarget(chat_id=target)
    if target.lstrip("-").isdigit():
        return _RunLogTelegramTarget(chat_id=int(target))

    parsed = urlparse(target)
    if (
        parsed.scheme in {"http", "https"}
        and parsed.netloc.lower() in TELEGRAM_TOPIC_HOSTS
    ):
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) == 1 and path_parts[0] != "c":
            return _RunLogTelegramTarget(chat_id=f"@{path_parts[0]}")
        topic = parse_telegram_topic_url(target)
        return _RunLogTelegramTarget(
            chat_id=topic.chat_id,
            message_thread_id=topic.message_thread_id,
        )

    raise ValueError(
        "TELEGRAM_RUN_LOG_TARGET must be @channel, numeric chat_id, "
        "https://t.me/<channel>, or https://t.me/c/<chat_id>/<thread_id>"
    )


def _should_attach_file(notification: RunLogNotification, attach_file: bool) -> bool:
    return (
        attach_file
        and notification.log_file is not None
        and notification.log_file.is_file()
    )


def _format_log_file_caption(log_file: Path) -> str:
    return f"📎 <b>运行日志</b>\n<code>{_escape(log_file.name)}</code>"


def _status_icon(status: str) -> str:
    return {
        "success": "✅",
        "warning": "⚠️",
        "failed": "❌",
    }.get(status, "ℹ️")


def _status_text(status: str) -> str:
    return {
        "success": "完成",
        "warning": "完成但有异常",
        "failed": "失败",
    }.get(status, status)


def _format_duration(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, second = divmod(total_seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minute}m {second}s"
    if minute:
        return f"{minute}m {second}s"
    return f"{second}s"


def _format_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _compact_text(value: str, *, max_length: int) -> str:
    text = " ".join(value.split()) or "unknown error"
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 1]}..."


def _escape(value: str) -> str:
    return html.escape(value)
