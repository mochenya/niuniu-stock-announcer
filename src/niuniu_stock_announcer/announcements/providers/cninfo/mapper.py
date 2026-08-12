"""CNInfo 原生公告到业务公告的映射。"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from cninfo_announcement.models import AnnouncementRecord
from cninfo_announcement.pdf import build_pdf_url

from niuniu_stock_announcer.announcements.providers.cninfo.schema import (
    CninfoNativeAnnouncement,
)
from niuniu_stock_announcer.announcements.schema import (
    AnnouncementQuery,
    AnnouncementSecurity,
    ChinaAnnouncement,
    CninfoSourceSnapshot,
    ProviderAnnouncement,
)

HIGHLIGHT_TAG_RE = re.compile(r"</?em>")


def map_cninfo_announcement(
    item: AnnouncementRecord, query: AnnouncementQuery
) -> ProviderAnnouncement:
    """校验并映射一条 CNInfo SDK 公告。

    Args:
        item: 锁定 SDK 已解析的原生公告对象。
        query: 产生该结果的 exchange、scope 与时间窗口。

    Returns:
        可跨层使用的业务公告和白名单来源快照。

    Raises:
        ValueError: 身份、标题、时间或 PDF 定位缺失或无效。
    """
    native = CninfoNativeAnnouncement.model_validate(item.model_dump(mode="python"))
    announcement_id = _require_text(native.announcementId, field="announcementId")
    title = HIGHLIGHT_TAG_RE.sub(
        "", _require_text(native.announcementTitle, field="announcementTitle")
    )
    if native.announcementTime is None:
        raise ValueError("CNInfo announcementTime 不能为空")
    # CNInfo 毫秒值已经编码了真实瞬间，直接按 epoch 转成 aware UTC，避免再次套用
    # 中国时区造成八小时偏移。
    published_at = datetime.fromtimestamp(native.announcementTime / 1000, tz=UTC)
    securities = ()
    if native.secCode and native.secCode.strip():
        securities = (
            AnnouncementSecurity(
                exchange=query.exchange,
                stock_code=native.secCode,
                stock_name=native.secName,
            ),
        )
    announcement = ChinaAnnouncement(
        provider_key="cninfo",
        provider_announcement_id=announcement_id,
        market_scope=query.market_scope,
        securities=securities,
        title=title,
        published_at=published_at,
        source_url=build_pdf_url(item),
    )
    snapshot = CninfoSourceSnapshot(
        announcement_id=announcement_id,
        sec_code=native.secCode,
        sec_name=native.secName,
        org_id=native.orgId,
        announcement_title=native.announcementTitle,
        announcement_time_ms=native.announcementTime,
        adjunct_url=native.adjunctUrl,
        adjunct_size=native.adjunctSize,
        adjunct_type=native.adjunctType,
        column_id=native.columnId,
        page_column=native.pageColumn,
        announcement_type=native.announcementType,
    )
    return ProviderAnnouncement(announcement=announcement, source_snapshot=snapshot)


def _require_text(value: str | None, *, field: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"CNInfo {field} 不能为空")
    return normalized
