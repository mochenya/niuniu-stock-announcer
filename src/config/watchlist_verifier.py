from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import get_args

from pydantic import BaseModel, ConfigDict

from announcements.search import query_search_task
from announcements.sources import (
    announcement_source_for_market,
    create_announcement_client,
)
from domain.common import (
    AnnouncementSource,
    Market,
    build_stock_key,
    normalize_required_text,
    validate_announcement_source_for_market,
)
from domain.search_models import SearchTask

RECENT_ANNOUNCEMENT_CHECK_DAYS = 60
PREVIEW_ANNOUNCEMENT_LIMIT = 3
ALLOWED_MARKETS = tuple(get_args(Market))


class AnnouncementPreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    announcement_date: str
    title: str


class StockAnnouncementCheckResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    code: str
    source: AnnouncementSource
    start_date: date
    end_date: date
    found_count: int
    preview_items: list[AnnouncementPreview]

    @property
    def stock_key(self) -> str:
        return build_stock_key(market=self.market, stock_code=self.code)

    @property
    def found(self) -> bool:
        return self.found_count > 0


class StockAnnouncementCheckError(RuntimeError):
    pass


def verify_stock_has_recent_announcements(
    *,
    market: str,
    code: str,
    source: str | None = None,
    days: int = RECENT_ANNOUNCEMENT_CHECK_DAYS,
) -> StockAnnouncementCheckResult:
    normalized_market = _normalize_market(market)
    normalized_code = normalize_required_text(code, field_name="code")
    if days <= 0:
        raise ValueError("days must be greater than 0")
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    resolved_source = _resolve_source(market=normalized_market, source=source)
    task = SearchTask(
        announcement_source=resolved_source,
        source_key="config-add-verify",
        market=normalized_market,
        stock_code=normalized_code,
        stock_key=build_stock_key(
            market=normalized_market,
            stock_code=normalized_code,
        ),
        search_mode="stock",
        title_exclude_keywords=[],
        config_snapshot={},
    )
    try:
        with create_announcement_client(resolved_source) as client:
            result = query_search_task(
                client,
                task,
                start_date=start_date,
                end_date=end_date,
            )
    except Exception as error:
        raise StockAnnouncementCheckError(str(error)) from error
    items = sorted(
        result.items,
        key=lambda item: item.announcement_time or 0,
        reverse=True,
    )
    return StockAnnouncementCheckResult(
        market=normalized_market,
        code=normalized_code,
        source=resolved_source,
        start_date=start_date,
        end_date=end_date,
        found_count=len(items),
        preview_items=[
            _build_preview(item) for item in items[:PREVIEW_ANNOUNCEMENT_LIMIT]
        ],
    )


def _normalize_market(market: str) -> Market:
    normalized_market = normalize_required_text(market, field_name="market").lower()
    if normalized_market not in ALLOWED_MARKETS:
        expected = ", ".join(ALLOWED_MARKETS)
        raise ValueError(
            f"Invalid market: {normalized_market}. Expected one of: {expected}."
        )
    return normalized_market  # type: ignore[return-value]


def _resolve_source(*, market: Market, source: str | None) -> AnnouncementSource:
    if source is None:
        return announcement_source_for_market(market)
    return validate_announcement_source_for_market(market=market, source=source)


def _build_preview(announcement) -> AnnouncementPreview:
    return AnnouncementPreview(
        announcement_date=_format_announcement_date(announcement.announcement_time),
        title=announcement.announcement_title or "-",
    )


def _format_announcement_date(announcement_time_ms: int | None) -> str:
    if announcement_time_ms is None:
        return "-"
    announcement_time = datetime.fromtimestamp(announcement_time_ms / 1000, tz=UTC)
    return announcement_time.date().isoformat()
