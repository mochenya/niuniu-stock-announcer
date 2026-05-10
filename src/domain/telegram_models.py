from __future__ import annotations

from enum import StrEnum
from typing import Literal

from cninfo_announcement.models import BusinessAnnouncement
from pydantic import BaseModel, ConfigDict

from domain.summary_models import AnnouncementSummary
from domain.common import AnnouncementSource, Market


class TelegramTargetKey(StrEnum):
    """数据库中保存的 Telegram 目标渠道标识。"""

    A_SHARE = "a_share"
    HK = "hk"


class TelegramTopicTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chat_id: int
    message_thread_id: int


class TelegramSummaryPayload(BaseModel):
    """发送 Telegram 消息所需的完整公告上下文。"""

    model_config = ConfigDict(extra="forbid")

    source: AnnouncementSource
    announcement_id: str
    market: Market
    stock_code: str
    stock_key: str
    company_name: str
    announcement: BusinessAnnouncement
    summary: AnnouncementSummary
    search_keyword: str | None = None


class TelegramSendResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    announcement_id: str
    kind: Literal["text", "document"]
    chat_id: int
    message_thread_id: int
    message_id: int


class TelegramDeliveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: TelegramSendResult | None = None
    pdf: TelegramSendResult | None = None
