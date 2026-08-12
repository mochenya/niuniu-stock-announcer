"""Telegram 公告 topic 与运行日志 target 解析。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from niuniu_stock_announcer.im.telegram.schema import TelegramTarget

TELEGRAM_TOPIC_HOSTS = frozenset({"t.me", "www.t.me", "telegram.me", "www.telegram.me"})


@dataclass(frozen=True, slots=True)
class RunLogTelegramTarget:
    """保存运行日志允许使用的 channel/chat/topic 地址。"""

    chat_id: int | str
    message_thread_id: int | None = None


def parse_telegram_topic_url(url: str) -> TelegramTarget:
    """把私有 Telegram topic URL 转换为 Bot API 地址。

    Args:
        url: 形如 `https://t.me/c/<chat_id>/<thread_id>` 的完整 URL。

    Returns:
        带 `-100` chat 前缀与 topic ID 的冻结 target。

    Raises:
        ValueError: scheme、host、路径形状或数字 ID 不合法。
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Telegram topic URL 必须以 http:// 或 https:// 开头")
    if parsed.netloc.lower() not in TELEGRAM_TOPIC_HOSTS:
        raise ValueError("Telegram topic URL host 必须是 t.me")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("Telegram topic URL 不能包含参数、query 或 fragment")

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 3 or path_parts[0] != "c":
        raise ValueError(
            "Telegram topic URL 必须形如 https://t.me/c/<chat_id>/<thread_id>"
        )
    chat_token, thread_token = path_parts[1:]
    if not chat_token.isdigit() or not thread_token.isdigit():
        raise ValueError("Telegram topic URL 必须包含数字 chat/thread ID")
    if int(chat_token) <= 0:
        raise ValueError("Telegram topic chat ID 必须大于 0")
    chat_id = int(f"-100{chat_token}")
    message_thread_id = int(thread_token)
    if message_thread_id <= 0:
        raise ValueError("Telegram topic thread ID 必须大于 0")
    return TelegramTarget(
        chat_id=chat_id,
        message_thread_id=message_thread_id,
    )


def parse_run_log_target(raw_target: str) -> RunLogTelegramTarget:
    """解析运行日志允许的 channel、chat 或 topic target。

    Args:
        raw_target: `@channel`、数字 chat ID、channel URL 或 topic URL。

    Returns:
        可直接交给 Bot API 的运行日志 target。

    Raises:
        ValueError: target 为空或不属于受支持格式。
    """
    target = raw_target.strip()
    if target.startswith("@") and len(target) > 1:
        return RunLogTelegramTarget(chat_id=target)
    if target.lstrip("-").isdigit():
        chat_id = int(target)
        if chat_id == 0:
            raise ValueError("Telegram run log chat_id 不能为 0")
        return RunLogTelegramTarget(chat_id=chat_id)

    parsed = urlparse(target)
    if (
        parsed.scheme in {"http", "https"}
        and parsed.netloc.lower() in TELEGRAM_TOPIC_HOSTS
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    ):
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) == 1 and path_parts[0] != "c":
            return RunLogTelegramTarget(chat_id=f"@{path_parts[0]}")
        topic = parse_telegram_topic_url(target)
        return RunLogTelegramTarget(
            chat_id=topic.chat_id,
            message_thread_id=topic.message_thread_id,
        )
    raise ValueError(
        "TELEGRAM_RUN_LOG_TARGET 必须是 @channel、数字 chat_id、channel URL 或 topic URL"
    )
