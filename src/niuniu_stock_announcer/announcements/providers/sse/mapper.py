"""SSE 原生公告到业务公告的映射。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from sse_announcement.models import SSEBulletinFile, build_announcement_id
from sse_announcement.pdf import build_pdf_url

from niuniu_stock_announcer.announcements.providers.sse.schema import (
    SseNativeAnnouncement,
)
from niuniu_stock_announcer.announcements.schema import (
    AnnouncementQuery,
    AnnouncementSecurity,
    ChinaAnnouncement,
    ProviderAnnouncement,
    SseSourceSnapshot,
)


def map_sse_announcement(
    item: SSEBulletinFile, query: AnnouncementQuery
) -> ProviderAnnouncement:
    """校验并映射一条 SSE 正文公告。

    Args:
        item: SDK 已从二维公告组中选出的正文文件。
        query: 产生该结果的 China 查询上下文。

    Returns:
        可跨层使用的业务公告和白名单来源快照。

    Raises:
        ValueError: 正文标记、身份、标题、日期或 PDF 定位无效。
    """
    native = SseNativeAnnouncement.model_validate(item.model_dump(mode="python"))
    if query.exchange != "sh":
        raise ValueError("SSE 只支持 exchange=sh")
    if native.ORG_FILE_TYPE != 0:
        raise ValueError("SSE mapper 只接受 ORG_FILE_TYPE=0 的正文")
    announcement_id = build_announcement_id(item)
    title = _require_text(native.TITLE, field="TITLE")
    sse_date = date.fromisoformat(_require_text(native.SSEDATE, field="SSEDATE"))
    # SSE 原生只给日期。锁定 SDK 已有契约，以 UTC 零点构造稳定瞬间；原始日期文本
    # 仍单独保存，后续如需改变解释规则必须显式迁移而不能悄然漂移。
    published_at = datetime.combine(sse_date, time.min, tzinfo=UTC)
    securities = ()
    if native.SECURITY_CODE and native.SECURITY_CODE.strip():
        securities = (
            AnnouncementSecurity(
                exchange="sh",
                stock_code=native.SECURITY_CODE,
                stock_name=native.SECURITY_NAME,
            ),
        )
    announcement = ChinaAnnouncement(
        provider_key="sse",
        provider_announcement_id=announcement_id,
        market_scope=query.market_scope,
        securities=securities,
        title=title,
        published_at=published_at,
        source_url=build_pdf_url(item),
    )
    snapshot = SseSourceSnapshot(
        provider_announcement_id=announcement_id,
        security_code=native.SECURITY_CODE,
        security_name=native.SECURITY_NAME,
        org_bulletin_id=native.ORG_BULLETIN_ID,
        title=native.TITLE,
        sse_date=native.SSEDATE,
        url=native.URL,
        bulletin_type_desc=native.BULLETIN_TYPE_DESC,
        is_holder_disclose=native.IS_HOLDER_DISCLOSE,
    )
    return ProviderAnnouncement(announcement=announcement, source_snapshot=snapshot)


def _require_text(value: str | None, *, field: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"SSE {field} 不能为空")
    return normalized
