"""不进入公告 outbox 的可选 Telegram 运行日志通知。"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from telegram import Bot
from telegram.constants import ParseMode

from niuniu_stock_announcer.im.telegram.sender import (
    _build_timeout_kwargs,
    _call_with_retry,
    _run_telegram_call,
)
from niuniu_stock_announcer.im.telegram.target import (
    parse_run_log_target,
)


@dataclass(frozen=True, slots=True)
class RunLogSyncStats:
    """保存运行日志中的同步统计。"""

    fetched: int
    filtered: int
    seeded: int
    errors: int
    new_refs: int | None = None


@dataclass(frozen=True, slots=True)
class RunLogStageStats:
    """保存运行日志中的阶段终态统计。"""

    completed: int
    failed: int
    unknown: int = 0


@dataclass(frozen=True, slots=True)
class RunLogNotification:
    """保存一条 CLI 运行日志通知的冻结输入。"""

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


class TelegramRunLogNotifier:
    """使用全局 Bot token 向独立 target 发送可选运维通知。"""

    def __init__(
        self,
        *,
        bot_token: str,
        target: str,
        timeout: float,
        attach_file: bool,
        bot_factory: Any | None = None,
    ) -> None:
        """绑定显式运行日志设置，不加载 Plan 或创建公告 outbox。

        Args:
            bot_token: 与公告投递共用的全局 Telegram Bot token。
            target: 独立的 channel、chat 或 topic target 文本。
            timeout: 四类 Bot API timeout 共用的正数秒数。
            attach_file: 日志文件存在时是否随通知附加。
            bot_factory: 可注入的 Bot 构造器；测试用它阻止真实网络。

        Raises:
            ValueError: token、target 或 timeout 不合法。
        """
        normalized_token = bot_token.strip()
        if not normalized_token:
            raise ValueError("Telegram Bot token 不能为空")
        if timeout <= 0:
            raise ValueError("Telegram timeout 必须大于 0")
        self._bot_token = normalized_token
        self._target = parse_run_log_target(target)
        self._timeout = timeout
        self._attach_file = attach_file
        self._bot_factory = bot_factory or Bot

    def notify(self, notification: RunLogNotification) -> bool:
        """发送运维文本及可选日志文件。

        Args:
            notification: 已汇总的 CLI 运行结果与可选本地日志路径。

        Returns:
            消息发送完成时返回 `True`。

        Raises:
            TelegramSendFailed: Telegram 明确拒绝或安全重试耗尽。
            TelegramSendOutcomeUnknown: 网络中断或超时使结果不可确认。
        """
        return _run_telegram_call(
            self._async_notify(notification),
            chat_id=self._target.chat_id,
            message_thread_id=self._target.message_thread_id,
            redactions=(self._bot_token,),
        )

    async def _async_notify(self, notification: RunLogNotification) -> bool:
        should_attach = _should_attach_file(notification, self._attach_file)
        async with self._bot_factory(token=self._bot_token) as bot:
            await _call_with_retry(
                lambda: bot.send_message(
                    chat_id=self._target.chat_id,
                    message_thread_id=self._target.message_thread_id,
                    text=format_run_log_message(
                        notification,
                        will_attach_file=should_attach,
                    ),
                    parse_mode=ParseMode.HTML,
                    **_build_timeout_kwargs(self._timeout),
                ),
                chat_id=self._target.chat_id,
                message_thread_id=self._target.message_thread_id,
                redactions=(self._bot_token,),
            )
            if should_attach:
                assert notification.log_file is not None
                with notification.log_file.open("rb") as document_file:

                    async def send_document_call():
                        document_file.seek(0)
                        return await bot.send_document(
                            chat_id=self._target.chat_id,
                            message_thread_id=self._target.message_thread_id,
                            document=document_file,
                            filename=notification.log_file.name,
                            caption=_format_log_file_caption(notification.log_file),
                            parse_mode=ParseMode.HTML,
                            **_build_timeout_kwargs(self._timeout),
                        )

                    await _call_with_retry(
                        send_document_call,
                        chat_id=self._target.chat_id,
                        message_thread_id=self._target.message_thread_id,
                        redactions=(self._bot_token,),
                    )
        return True


def format_run_log_message(
    notification: RunLogNotification,
    *,
    will_attach_file: bool,
) -> str:
    """把 CLI 统计格式化为稳定 Telegram HTML 文本。

    Args:
        notification: 已汇总的 CLI 运行结果。
        will_attach_file: 本次是否实际附加完整日志文件。

    Returns:
        可使用 HTML parse mode 发送的运维消息。
    """
    lines = [
        "<b>📣 牛牛股票公告员</b>",
        f"<code>{_escape(notification.command)}</code>",
        "",
        f"{_status_icon(notification.status)} <b>运行状态：</b>"
        f"{_status_text(notification.status)}",
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
                f"🗂 <i>日志文件：</i>"
                f"<code>{_escape(str(notification.log_file))}</code>",
            ]
        )
    return "\n".join(lines)


def _should_attach_file(
    notification: RunLogNotification,
    attach_file: bool,
) -> bool:
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
    return f"{text[: max_length - 3]}..."


def _escape(value: str) -> str:
    return html.escape(value)
