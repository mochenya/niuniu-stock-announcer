"""导入全部 v2 ORM Model，确保 Alembic metadata 完整。"""

from niuniu_stock_announcer.db.model.base import Base
from niuniu_stock_announcer.db.model.china import (
    ChinaAnnouncementMatchModel,
    ChinaAnnouncementModel,
    ChinaSummaryModel,
    CninfoAnnouncementModel,
    SseAnnouncementModel,
    SzseAnnouncementModel,
)
from niuniu_stock_announcer.db.model.telegram import (
    TelegramDeliveryModel,
    TelegramDocumentMessageModel,
    TelegramSummaryMessageModel,
)

__all__ = [
    "Base",
    "ChinaAnnouncementMatchModel",
    "ChinaAnnouncementModel",
    "ChinaSummaryModel",
    "CninfoAnnouncementModel",
    "SseAnnouncementModel",
    "SzseAnnouncementModel",
    "TelegramDeliveryModel",
    "TelegramDocumentMessageModel",
    "TelegramSummaryMessageModel",
]
