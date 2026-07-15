from __future__ import annotations

from pathlib import Path

from cninfo_announcement.models import BusinessAnnouncement
from pydantic import BaseModel, ConfigDict, Field

from domain.common import AnnouncementSource, Market, SummaryStatus
from domain.summary_models import AnnouncementSummary
from domain.telegram_models import TelegramTargetKey
from domain.workflow_models import AnnouncementRef


class AnnouncementCandidateRecord(BaseModel):
    """摘要和投递查询共同需要的公告上下文。

    该基类只表示数据库查询投影中的稳定公告字段，不承载任何阶段状态。
    """

    model_config = ConfigDict(extra="forbid")

    source: AnnouncementSource
    announcement_id: str
    announcement: BusinessAnnouncement
    market: Market
    stock_code: str
    stock_key: str
    company_name: str
    search_keyword: str | None = None

    @property
    def ref(self) -> AnnouncementRef:
        return AnnouncementRef(
            source=self.source,
            announcement_id=self.announcement_id,
        )


class SummaryCandidateRecord(AnnouncementCandidateRecord):
    """摘要阶段从数据库领取的一条最小候选记录。"""

    pdf_local_path: Path | None = None
    summary_failure_count: int = Field(default=0, ge=0)


class DeliveryCandidateRecord(AnnouncementCandidateRecord):
    """投递阶段从数据库领取的一条最小候选记录。"""

    summary_status: SummaryStatus
    pdf_local_path: Path
    summary_text: str | None = None
    summary_tags: list[str] = Field(default_factory=list)
    delivery_id: int
    target_key: TelegramTargetKey
    text_message_id: int | None = None
    pdf_message_id: int | None = None

    @property
    def stored_summary(self) -> AnnouncementSummary | None:
        """只有库里的摘要字段满足投递要求时，才返回可用摘要。"""
        if self.summary_text is None:
            return None
        if len(self.summary_tags) < 3 or len(self.summary_tags) > 6:
            return None
        return AnnouncementSummary(
            summary=self.summary_text,
            tags=self.summary_tags,
        )
