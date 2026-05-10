from __future__ import annotations

from db.announcements import AnnouncementWriteRepository
from db.deliveries import TelegramDeliveryRepository
from db.summaries import SummaryRepository


class AnnouncementRepository(
    AnnouncementWriteRepository,
    SummaryRepository,
    TelegramDeliveryRepository,
):
    """聚合各阶段仓储方法，工作流只依赖这一个仓储入口。"""

    pass
