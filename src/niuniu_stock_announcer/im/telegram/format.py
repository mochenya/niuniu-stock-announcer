"""Telegram 公告 HTML 文本与 document caption 格式。"""

from __future__ import annotations

import hashlib
import html
from zoneinfo import ZoneInfo

from niuniu_stock_announcer.delivery.schema import ChinaDeliveryRenderInput

SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
FULL_STOCK_CODE_PREFIXES = {
    "sh": "SH",
    "sz": "SZ",
    "bj": "BJ",
    "hk": "HK",
}


def format_telegram_summary_text(payload: ChinaDeliveryRenderInput) -> str:
    """把冻结 China 事实格式化为 Telegram HTML 摘要文本。

    Args:
        payload: 由 Delivery Service 从持久化快照投影出的渲染输入。

    Returns:
        可直接使用 HTML parse mode 发送的不可变文本。
    """
    lines = _build_header_lines(payload)
    if payload.summary_status == "skipped":
        lines.extend(["", "⚠️ 摘要生成失败，请直接查看 PDF。"])
        return "\n".join(lines)
    tags = " ".join(f"#{_escape(tag)}" for tag in payload.summary_tags)
    lines.extend([f"🏷 标签: {tags}", "", _escape(payload.summary_text or "")])
    return "\n".join(lines)


def format_telegram_document_caption(payload: ChinaDeliveryRenderInput) -> str:
    """把冻结 China 事实格式化为原文附件 caption。

    Args:
        payload: 由 Delivery Service 从持久化快照投影出的渲染输入。

    Returns:
        与摘要消息前五行一致的 HTML caption。
    """
    return "\n".join(_build_header_lines(payload))


def _build_header_lines(payload: ChinaDeliveryRenderInput) -> list[str]:
    title = _escape(_strip_highlight_tags(payload.title))
    company = _escape(payload.company_name or "未提供")
    company_line = f"🏢 公司: <b>{company}</b>"
    if payload.exchange is not None and payload.stock_code is not None:
        full_code = f"{FULL_STOCK_CODE_PREFIXES[payload.exchange]}{payload.stock_code}"
        company_line = f"{company_line} - #{_escape(full_code)}"

    hit_parts: list[str] = []
    if payload.stock_code is not None:
        hit_parts.append(f"stock #{_escape(payload.stock_code)}")
    hit_parts.extend(
        f"keyword #{_escape(keyword)}" for keyword in payload.matched_search_keywords
    )
    fingerprint = hashlib.sha256(
        payload.provider_announcement_id.encode("utf-8")
    ).hexdigest()[:6]
    hit_parts.append(f"#{fingerprint}")
    published_at = payload.published_at.astimezone(SHANGHAI_TIMEZONE).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    return [
        f"📊 <b>{title}</b>",
        company_line,
        f"📡 搜索源: {_escape(payload.provider_key)}",
        f"⏱️ 时间: {_escape(published_at)}",
        f"🔍 命中: {' '.join(hit_parts)}",
    ]


def _escape(value: str) -> str:
    return html.escape(value)


def _strip_highlight_tags(value: str) -> str:
    return value.replace("<em>", "").replace("</em>", "")
