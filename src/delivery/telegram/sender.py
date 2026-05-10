from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut

from config.runtime import load_runtime_config
from delivery.telegram.format import (
    format_telegram_pdf_caption,
    format_telegram_summary_text,
)
from delivery.telegram.target import (
    resolve_telegram_target,
)
from domain.config_models import RuntimeConfig
from domain.telegram_models import (
    TelegramDeliveryResult,
    TelegramSendResult,
    TelegramSummaryPayload,
    TelegramTopicTarget,
)

T = TypeVar("T")


class TelegramSendOutcomeUnknown(RuntimeError):
    """发送请求可能已被 Telegram 接收，但本地无法确认最终结果。"""

    pass


async def async_send_telegram_delivery(
    payload: TelegramSummaryPayload,
    pdf_path: str | Path,
    *,
    send_text: bool,
    send_pdf: bool,
    env_file: str | Path | None = None,
    config: RuntimeConfig | None = None,
    on_text_sent: Callable[[TelegramSendResult], None] | None = None,
    on_pdf_sent: Callable[[TelegramSendResult], None] | None = None,
) -> TelegramDeliveryResult:
    """发送摘要文本和 PDF，并在每一步成功后回调调用方落库。

    send_text/send_pdf 由工作流根据已保存的 message_id 决定，避免重试时重复发送。
    """
    if not send_text and not send_pdf:
        return TelegramDeliveryResult()

    config = config or load_runtime_config(env_file=env_file)
    bot_token, _, target = resolve_telegram_target(config, payload.market)
    async with Bot(token=bot_token) as bot:
        text_result = None
        if send_text:
            text_result = await _send_text_message(
                announcement_id=payload.announcement_id,
                text=format_telegram_summary_text(payload),
                target=target,
                timeout=config.telegram.timeout,
                bot=bot,
            )
            if on_text_sent is not None:
                on_text_sent(text_result)

        pdf_result = None
        if send_pdf:
            pdf_result = await _send_pdf_document(
                announcement_id=payload.announcement_id,
                pdf_path=Path(pdf_path),
                caption=format_telegram_pdf_caption(payload),
                target=target,
                timeout=config.telegram.timeout,
                bot=bot,
            )
            if on_pdf_sent is not None:
                on_pdf_sent(pdf_result)

    return TelegramDeliveryResult(text=text_result, pdf=pdf_result)


def send_telegram_delivery(
    payload: TelegramSummaryPayload,
    pdf_path: str | Path,
    *,
    send_text: bool,
    send_pdf: bool,
    env_file: str | Path | None = None,
    config: RuntimeConfig | None = None,
    on_text_sent: Callable[[TelegramSendResult], None] | None = None,
    on_pdf_sent: Callable[[TelegramSendResult], None] | None = None,
) -> TelegramDeliveryResult:
    """同步入口，供非异步工作流调用 Telegram 投递。"""
    return _run_async(
        async_send_telegram_delivery(
            payload,
            pdf_path=pdf_path,
            send_text=send_text,
            send_pdf=send_pdf,
            env_file=env_file,
            config=config,
            on_text_sent=on_text_sent,
            on_pdf_sent=on_pdf_sent,
        )
    )


async def _send_text_message(
    *,
    announcement_id: str,
    text: str,
    target: TelegramTopicTarget,
    timeout: float,
    bot: Bot,
) -> TelegramSendResult:
    """发送摘要文本，并返回可落库的 Telegram message_id。"""
    message = await _call_with_retry(
        lambda: bot.send_message(
            chat_id=target.chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            message_thread_id=target.message_thread_id,
            **_build_timeout_kwargs(timeout),
        ),
        chat_id=target.chat_id,
        message_thread_id=target.message_thread_id,
    )
    return TelegramSendResult(
        announcement_id=announcement_id,
        kind="text",
        chat_id=target.chat_id,
        message_thread_id=target.message_thread_id,
        message_id=message.message_id,
    )


async def _send_pdf_document(
    *,
    announcement_id: str,
    pdf_path: Path,
    caption: str,
    target: TelegramTopicTarget,
    timeout: float,
    bot: Bot,
) -> TelegramSendResult:
    """发送 PDF 文件，并返回可落库的 Telegram message_id。"""
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    with pdf_path.open("rb") as document_file:

        async def send_document_call():
            document_file.seek(0)
            return await bot.send_document(
                chat_id=target.chat_id,
                document=document_file,
                filename=pdf_path.name,
                caption=caption,
                parse_mode=ParseMode.HTML,
                message_thread_id=target.message_thread_id,
                **_build_timeout_kwargs(timeout),
            )

        message = await _call_with_retry(
            send_document_call,
            chat_id=target.chat_id,
            message_thread_id=target.message_thread_id,
        )
    return TelegramSendResult(
        announcement_id=announcement_id,
        kind="document",
        chat_id=target.chat_id,
        message_thread_id=target.message_thread_id,
        message_id=message.message_id,
    )


def _build_timeout_kwargs(timeout: float) -> dict[str, float]:
    return {
        "read_timeout": timeout,
        "write_timeout": timeout,
        "connect_timeout": timeout,
        "pool_timeout": timeout,
    }


async def _call_with_retry(send_call, *, chat_id: int, message_thread_id: int):
    """执行 Telegram API 调用。

    RetryAfter 可安全等待后重试；网络中断或超时会升级为 unknown，交给工作流处理。
    """
    last_error: Exception | None = None
    for _ in range(5):
        try:
            return await send_call()
        except RetryAfter as exc:
            # 收到 Telegram 明确等待要求时可以安全重试，文件对象会在重试前 seek(0)。
            last_error = exc
            retry_after = exc.retry_after
            await asyncio.sleep(
                retry_after.total_seconds()
                if hasattr(retry_after, "total_seconds")
                else retry_after
            )
        except (TimedOut, NetworkError) as exc:
            # 超时或网络中断时无法判断 Telegram 是否已完成发送，交给工作流标记 unknown。
            raise TelegramSendOutcomeUnknown(
                f"Telegram send outcome is unknown for chat_id={chat_id}, "
                f"message_thread_id={message_thread_id}: {exc}"
            ) from exc
        except TelegramError as exc:
            raise RuntimeError(
                f"Telegram send failed for chat_id={chat_id}, "
                f"message_thread_id={message_thread_id}: {exc}"
            ) from exc
    raise RuntimeError(
        f"Telegram send failed for chat_id={chat_id}, "
        f"message_thread_id={message_thread_id}: exhausted retries after {last_error}"
    ) from last_error


def _run_async(coro: Awaitable[T]) -> T:
    """在同步 CLI 中运行异步发送；已有事件循环时显式报错。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "synchronous Telegram helpers cannot run inside an active event loop"
    )
