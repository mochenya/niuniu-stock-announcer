from __future__ import annotations

import hashlib
import html
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from domain.common import Market
from domain.telegram_models import TelegramSummaryPayload

SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
FULL_STOCK_CODE_PREFIXES = {
    "sh": "SH",
    "sz": "SZ",
    "bj": "BJ",
    "hk": "HK",
}


def format_telegram_summary_text(payload: TelegramSummaryPayload) -> str:
    lines = _build_message_header_lines(payload)
    tags = " ".join(_format_tag(tag) for tag in payload.summary.tags)
    lines.append(f"🏷 标签: {tags}")
    lines.append("")
    lines.append(_escape(payload.summary.summary))
    return "\n".join(lines)


def format_telegram_pdf_caption(payload: TelegramSummaryPayload) -> str:
    return "\n".join(_build_message_header_lines(payload))


def _build_message_header_lines(payload: TelegramSummaryPayload) -> list[str]:
    title = _escape(
        _strip_highlight_tags(_require_text(payload.announcement.announcement_title))
    )
    company_name = _escape(_require_text(payload.company_name))
    stock_code = _escape(_format_full_stock_code(payload.market, payload.stock_code))
    announcement_time = _escape(
        _format_announcement_time(payload.announcement.announcement_time)
    )
    source_fingerprint = _announcement_id_fingerprint(payload.announcement_id)
    hit_parts = [f"stock #{_escape(payload.stock_code)}"]
    if payload.search_keyword:
        hit_parts.append(f"keyword #{_escape(payload.search_keyword)}")
    hit_parts.append(f"#{source_fingerprint}")
    return [
        f"📊 <b>{title}</b>",
        f"🏢 公司: <b>{company_name}</b> - #{stock_code}",
        f"📡 搜索源: {payload.source}",
        f"⏱️ 时间: {announcement_time}",
        f"🔍 命中: {' '.join(hit_parts)}",
    ]


def _format_full_stock_code(market: Market, sec_code: str | None) -> str:
    code = _require_text(sec_code)
    return f"{FULL_STOCK_CODE_PREFIXES[market]}{code}"


def _format_announcement_time(announcement_time_ms: int | None) -> str:
    if announcement_time_ms is None:
        raise ValueError("announcement_time is required")
    announcement_time = datetime.fromtimestamp(
        announcement_time_ms / 1000,
        tz=UTC,
    ).astimezone(SHANGHAI_TIMEZONE)
    return announcement_time.strftime("%Y-%m-%d %H:%M:%S")


def _announcement_id_fingerprint(announcement_id: str) -> str:
    value = _require_text(announcement_id)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:6]


def _require_text(value: str | None) -> str:
    if value is None:
        raise ValueError("text value is required")
    normalized = value.strip()
    if not normalized:
        raise ValueError("text value cannot be empty")
    return normalized


def _escape(value: str) -> str:
    return html.escape(value)


def _format_tag(value: str) -> str:
    return f"#{_escape(value)}"


def _strip_highlight_tags(value: str) -> str:
    return value.replace("<em>", "").replace("</em>", "")
