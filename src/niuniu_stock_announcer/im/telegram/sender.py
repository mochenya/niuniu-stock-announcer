"""只接收冻结 payload 的 Telegram Bot API 发送适配器。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, BinaryIO, TypeVar

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError, TimedOut

from niuniu_stock_announcer.delivery.document import open_verified_document
from niuniu_stock_announcer.im.telegram.schema import (
    TelegramDocumentSendRequest,
    TelegramSendResult,
    TelegramTarget,
    TelegramTextSendRequest,
)

T = TypeVar("T")
MAX_RETRY_AFTER_ATTEMPTS = 5
MAX_RETRY_AFTER_SECONDS = 60.0


class TelegramSendFailed(RuntimeError):
    """表示 Telegram 明确拒绝发送或安全重试已耗尽。"""


class TelegramSendOutcomeUnknown(RuntimeError):
    """表示请求可能已被 Telegram 接收但本地无法确认结果。"""


class TelegramSender:
    """使用一个全局 Bot token 发送独立文本或 document 消息。"""

    def __init__(
        self,
        *,
        bot_token: str,
        timeout: float,
        document_storage_root: Path,
        bot_factory: Callable[..., Any] | None = None,
    ) -> None:
        """绑定显式基础设施设置，不读取环境、Plan 或数据库。

        Args:
            bot_token: 全系统唯一的 Telegram Bot token。
            timeout: 四类 Bot API timeout 共用的正数秒数。
            document_storage_root: document 相对路径必须落入的本地根目录。
            bot_factory: 可注入的 Bot 构造器；测试用它阻止真实网络。

        Raises:
            ValueError: token 为空或 timeout 非正数。
        """
        normalized_token = bot_token.strip()
        if not normalized_token:
            raise ValueError("Telegram Bot token 不能为空")
        if timeout <= 0:
            raise ValueError("Telegram timeout 必须大于 0")
        self._bot_token = normalized_token
        self._timeout = timeout
        self._document_storage_root = document_storage_root
        self._bot_factory = bot_factory or Bot

    def send_text(self, request: TelegramTextSendRequest) -> TelegramSendResult:
        """发送一条冻结 HTML 文本并返回外部消息身份。

        Args:
            request: 已包含冻结 target 与文本的发送请求。

        Returns:
            Telegram 明确确认的 chat/thread/message ID 与可选链接。

        Raises:
            TelegramSendFailed: Telegram 明确拒绝或安全重试耗尽。
            TelegramSendOutcomeUnknown: 网络中断或超时使发送结果不可确认。
        """
        return _run_telegram_call(
            self._async_send_text(request),
            chat_id=request.target.chat_id,
            message_thread_id=request.target.message_thread_id,
            redactions=(self._bot_token,),
        )

    def send_document(self, request: TelegramDocumentSendRequest) -> TelegramSendResult:
        """复验并发送一份冻结本地 document。

        Args:
            request: 已包含冻结路径、size、hash、caption 与 target 的请求。

        Returns:
            Telegram 明确确认的 chat/thread/message ID 与可选链接。

        Raises:
            FileNotFoundError: 冻结本地文件不存在。
            ValueError: 路径、size 或 SHA-256 复验失败。
            TelegramSendFailed: Telegram 明确拒绝或安全重试耗尽。
            TelegramSendOutcomeUnknown: 网络中断或超时使发送结果不可确认。
        """
        try:
            with open_verified_document(
                request,
                storage_root=self._document_storage_root,
            ) as document_file:
                return _run_telegram_call(
                    self._async_send_document(request, document_file),
                    chat_id=request.target.chat_id,
                    message_thread_id=request.target.message_thread_id,
                    redactions=(self._bot_token,),
                )
        except TelegramSendFailed, TelegramSendOutcomeUnknown:
            raise
        except (FileNotFoundError, ValueError) as exc:
            # 路径、size、hash 都在创建 Bot 前完成复验，可以证明请求尚未发送。
            raise TelegramSendFailed(
                f"Telegram document 发送前校验失败: {_compact_error(exc)}"
            ) from exc

    async def _async_send_text(
        self, request: TelegramTextSendRequest
    ) -> TelegramSendResult:
        async with self._bot_factory(token=self._bot_token) as bot:
            message = await _call_with_retry(
                lambda: bot.send_message(
                    chat_id=request.target.chat_id,
                    text=request.text_content,
                    parse_mode=ParseMode.HTML,
                    message_thread_id=request.target.message_thread_id,
                    **_build_timeout_kwargs(self._timeout),
                ),
                chat_id=request.target.chat_id,
                message_thread_id=request.target.message_thread_id,
                redactions=(self._bot_token,),
            )
        return _result_from_confirmed_message(message, request.target)

    async def _async_send_document(
        self,
        request: TelegramDocumentSendRequest,
        document_file: BinaryIO,
    ) -> TelegramSendResult:
        async with self._bot_factory(token=self._bot_token) as bot:

            async def send_document_call():
                document_file.seek(0)
                return await bot.send_document(
                    chat_id=request.target.chat_id,
                    document=document_file,
                    filename=request.document_filename,
                    caption=request.document_caption,
                    parse_mode=ParseMode.HTML,
                    message_thread_id=request.target.message_thread_id,
                    **_build_timeout_kwargs(self._timeout),
                )

            message = await _call_with_retry(
                send_document_call,
                chat_id=request.target.chat_id,
                message_thread_id=request.target.message_thread_id,
                redactions=(self._bot_token,),
            )
        return _result_from_confirmed_message(message, request.target)


def _build_timeout_kwargs(timeout: float) -> dict[str, float]:
    return {
        "read_timeout": timeout,
        "write_timeout": timeout,
        "connect_timeout": timeout,
        "pool_timeout": timeout,
    }


async def _call_with_retry(
    send_call: Callable[[], Awaitable[T]],
    *,
    chat_id: int | str,
    message_thread_id: int | None,
    redactions: tuple[str, ...] = (),
) -> T:
    """执行一次可安全处理 RetryAfter 的 Telegram API 调用。

    Args:
        send_call: 每次调用都会重新定位 document 句柄的异步发送函数。
        chat_id: 仅用于不含 credential 的受控错误上下文。
        message_thread_id: 仅用于受控错误上下文的可选 topic ID。
        redactions: 必须从异常文本中移除的 secret 值。

    Returns:
        Telegram SDK 返回的消息对象。

    Raises:
        TelegramSendFailed: 明确 API 失败或 RetryAfter 次数耗尽。
        TelegramSendOutcomeUnknown: timeout/network 使外部结果不可判断。
    """
    last_error: Exception | None = None
    for attempt in range(MAX_RETRY_AFTER_ATTEMPTS):
        try:
            return await send_call()
        except RetryAfter as exc:
            # Telegram 明确告知“尚未接受，请稍后重试”时才允许自动重试。
            last_error = exc
            retry_after = exc.retry_after
            wait_seconds = float(
                retry_after.total_seconds()
                if hasattr(retry_after, "total_seconds")
                else retry_after
            )
            if wait_seconds < 0 or wait_seconds > MAX_RETRY_AFTER_SECONDS:
                raise TelegramSendFailed(
                    "Telegram RetryAfter 等待时间超出安全上限: "
                    f"chat_id={chat_id}, message_thread_id={message_thread_id}"
                ) from exc
            if attempt + 1 < MAX_RETRY_AFTER_ATTEMPTS:
                await asyncio.sleep(wait_seconds)
        except BadRequest as exc:
            # python-telegram-bot 把 HTTP 400 的 BadRequest 建模为 NetworkError 子类，
            # 但服务端已明确拒绝该请求，因此必须先于 NetworkError 判为确定失败。
            raise TelegramSendFailed(
                "Telegram 明确发送失败: "
                f"chat_id={chat_id}, message_thread_id={message_thread_id}, "
                f"error={_compact_error(exc, redactions=redactions)}"
            ) from exc
        except (TimedOut, NetworkError) as exc:
            # 请求可能已经到达 Telegram；把它当 failed 会在 retry 时造成重复通知。
            raise TelegramSendOutcomeUnknown(
                "Telegram 发送结果不可确认: "
                f"chat_id={chat_id}, message_thread_id={message_thread_id}, "
                f"error={_compact_error(exc, redactions=redactions)}"
            ) from exc
        except TelegramError as exc:
            raise TelegramSendFailed(
                "Telegram 明确发送失败: "
                f"chat_id={chat_id}, message_thread_id={message_thread_id}, "
                f"error={_compact_error(exc, redactions=redactions)}"
            ) from exc
    raise TelegramSendFailed(
        "Telegram 安全重试次数已耗尽: "
        f"chat_id={chat_id}, message_thread_id={message_thread_id}, "
        f"error={_compact_error(last_error, redactions=redactions)}"
    ) from last_error


def _result_from_confirmed_message(
    message: Any,
    target: TelegramTarget,
) -> TelegramSendResult:
    result_chat_id = getattr(message, "chat_id", target.chat_id)
    result_thread_id = getattr(message, "message_thread_id", None)
    if result_thread_id is None:
        result_thread_id = target.message_thread_id
    try:
        message_url = message.link
    except AttributeError, RuntimeError, ValueError:
        message_url = None
    try:
        return TelegramSendResult(
            chat_id=result_chat_id,
            message_thread_id=result_thread_id,
            message_id=message.message_id,
            message_url=message_url or None,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        # SDK 已返回 Message，说明外部发送已经发生；即使结果字段异常也不能安全重发。
        raise TelegramSendOutcomeUnknown(
            "Telegram 已返回消息但外部身份无法持久化"
        ) from exc


def _compact_error(
    exc: Exception | None,
    *,
    redactions: tuple[str, ...] = (),
) -> str:
    if exc is None:
        return "unknown"
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    for secret in redactions:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[:500]


def _run_telegram_call(
    coro: Awaitable[T],
    *,
    chat_id: int | str,
    message_thread_id: int | None,
    redactions: tuple[str, ...] = (),
) -> T:
    try:
        return _run_async(coro)
    except TelegramSendFailed, TelegramSendOutcomeUnknown:
        raise
    except BadRequest as exc:
        raise TelegramSendFailed(
            "Telegram 明确发送失败: "
            f"chat_id={chat_id}, message_thread_id={message_thread_id}, "
            f"error={_compact_error(exc, redactions=redactions)}"
        ) from exc
    except (TimedOut, NetworkError) as exc:
        raise TelegramSendOutcomeUnknown(
            "Telegram 发送结果不可确认: "
            f"chat_id={chat_id}, message_thread_id={message_thread_id}, "
            f"error={_compact_error(exc, redactions=redactions)}"
        ) from exc
    except TelegramError as exc:
        raise TelegramSendFailed(
            "Telegram 明确发送失败: "
            f"chat_id={chat_id}, message_thread_id={message_thread_id}, "
            f"error={_compact_error(exc, redactions=redactions)}"
        ) from exc
    except Exception as exc:
        # typed 边界之外的异常可能发生在 Bot 初始化、请求或响应解析任一阶段；
        # 先脱敏再按 unknown 隔离，避免泄漏 token 或把可能已发送的消息自动重发。
        raise TelegramSendOutcomeUnknown(
            "Telegram 发送阶段出现未分类异常: "
            f"chat_id={chat_id}, message_thread_id={message_thread_id}, "
            f"error={_compact_error(exc, redactions=redactions)}"
        ) from exc


def _run_async(coro: Awaitable[T]) -> T:
    """在同步 CLI 中运行异步 Bot 调用并拒绝嵌套事件循环。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    close = getattr(coro, "close", None)
    if close is not None:
        close()
    raise RuntimeError("同步 Telegram sender 不能在活动事件循环中运行")
