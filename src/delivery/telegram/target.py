from __future__ import annotations

from urllib.parse import urlparse

from domain.common import Market
from domain.config_models import RuntimeConfig
from domain.telegram_models import (
    TelegramTargetKey,
    TelegramTopicTarget,
)

TELEGRAM_TOPIC_HOSTS = {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}


def parse_telegram_topic_url(url: str) -> TelegramTopicTarget:
    """解析 Telegram topic 链接，转换为 Bot API 所需的 chat/thread ID。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Telegram topic URL must start with http:// or https://")
    if parsed.netloc.lower() not in TELEGRAM_TOPIC_HOSTS:
        raise ValueError("Telegram topic URL host must be t.me")

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 3 or path_parts[0] != "c":
        raise ValueError(
            "Telegram topic URL must look like https://t.me/c/<chat_id>/<thread_id>"
        )

    # 这里把 t.me/c/<chat_id>/<thread_id> 中的 chat_id 补成 Bot API 的 -100 前缀格式。
    chat_token = path_parts[1]
    thread_token = path_parts[2]
    if not chat_token.isdigit() or not thread_token.isdigit():
        raise ValueError("Telegram topic URL must include numeric chat and thread IDs")

    return TelegramTopicTarget(
        chat_id=int(f"-100{chat_token}"),
        message_thread_id=int(thread_token),
    )


def resolve_telegram_target(
    config: RuntimeConfig,
    market: Market,
) -> tuple[str, TelegramTargetKey, TelegramTopicTarget]:
    """根据市场选择 A 股或港股 Telegram 目标。"""
    if market == "hk":
        channel_config = config.telegram.hk
        target_key = TelegramTargetKey.HK
        env_prefix = "TELEGRAM_HK"
    else:
        channel_config = config.telegram.a_share
        target_key = TelegramTargetKey.A_SHARE
        env_prefix = "TELEGRAM_A_SHARE"

    missing_fields = [
        name
        for name, value in (
            (f"{env_prefix}_BOT_TOKEN", channel_config.bot_token),
            (f"{env_prefix}_TOPIC_URL", channel_config.topic_url),
        )
        if not value
    ]
    if missing_fields:
        raise ValueError(f"missing Telegram config: {', '.join(missing_fields)}")

    return (
        channel_config.bot_token,
        target_key,
        parse_telegram_topic_url(channel_config.topic_url),
    )
