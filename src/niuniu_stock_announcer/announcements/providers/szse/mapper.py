"""SZSE 原生公告到业务公告的映射。"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from szse_announcement.models import SZSEAnnouncementRecord, build_announcement_id
from szse_announcement.pdf import build_pdf_url

from niuniu_stock_announcer.announcements.providers.szse.schema import (
    SzseNativeAnnouncement,
)
from niuniu_stock_announcer.announcements.schema import (
    AnnouncementQuery,
    AnnouncementSecurity,
    ChinaAnnouncement,
    ProviderAnnouncement,
    SzseSourceSnapshot,
)

SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


def map_szse_announcement(
    item: SZSEAnnouncementRecord, query: AnnouncementQuery
) -> ProviderAnnouncement:
    """校验并映射一条 SZSE 公告，同时保留证券数组形状。

    Args:
        item: 锁定 SDK 已解析的原生公告对象。
        query: 产生该结果的 China 查询上下文。

    Returns:
        可跨层使用的业务公告和白名单来源快照。

    Raises:
        ValueError: exchange、身份、标题、时间或 PDF 定位无效。
    """
    native = SzseNativeAnnouncement.model_validate(item.model_dump(mode="python"))
    if query.exchange != "sz":
        raise ValueError("SZSE 只支持 exchange=sz")
    announcement_id = build_announcement_id(item)
    title = _require_text(native.title, field="title")
    published_at = _parse_publish_time(native.publishTime)
    securities = _build_securities(native.secCode, native.secName)
    announcement = ChinaAnnouncement(
        provider_key="szse",
        provider_announcement_id=announcement_id,
        market_scope=query.market_scope,
        securities=securities,
        title=title,
        published_at=published_at,
        source_url=build_pdf_url(item),
    )
    snapshot = SzseSourceSnapshot(
        provider_announcement_id=announcement_id,
        ann_id=None if native.annId is None else str(native.annId),
        source_record_id=native.id,
        sec_codes=tuple(native.secCode),
        sec_names=tuple(native.secName),
        title=native.title,
        publish_time=native.publishTime,
        attach_path=native.attachPath,
        attach_format=native.attachFormat,
        attach_size=native.attachSize,
        bond_type=native.bondType,
        big_industry_code=native.bigIndustryCode,
        big_category_id=native.bigCategoryId,
        small_category_id=native.smallCategoryId,
        channel_code=native.channelCode,
    )
    return ProviderAnnouncement(announcement=announcement, source_snapshot=snapshot)


def _parse_publish_time(value: str | None) -> datetime:
    normalized = _require_text(value, field="publishTime")
    try:
        parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        parsed = datetime.combine(date.fromisoformat(normalized), time.min)
    # SZSE 文本没有 offset，但语义是中国市场本地时间；在 mapper 边界明确套用
    # Asia/Shanghai，避免数据库或调用方各自猜测。
    return parsed.replace(tzinfo=SHANGHAI_TIMEZONE)


def _build_securities(
    stock_codes: list[str], stock_names: list[str]
) -> tuple[AnnouncementSecurity, ...]:
    securities: list[AnnouncementSecurity] = []
    indexes: dict[str, int] = {}
    for index, code in enumerate(stock_codes):
        normalized_code = code.strip()
        if not normalized_code:
            continue
        name = stock_names[index] if index < len(stock_names) else None
        existing_index = indexes.get(normalized_code)
        if existing_index is None:
            indexes[normalized_code] = len(securities)
            securities.append(
                AnnouncementSecurity(
                    exchange="sz", stock_code=normalized_code, stock_name=name
                )
            )
            continue
        if securities[existing_index].stock_name is None and name:
            securities[existing_index] = securities[existing_index].model_copy(
                update={"stock_name": name}
            )
    return tuple(securities)


def _require_text(value: str | None, *, field: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"SZSE {field} 不能为空")
    return normalized
