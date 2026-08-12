"""CNInfo SDK 原生公告的严格边界 Schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class CninfoNativeAnnouncement(BaseModel):
    """镜像锁定 SDK `AnnouncementRecord` 的完整已知字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str | None = None
    secCode: str | None = None
    secName: str | None = None
    orgId: str | None = None
    announcementId: str | None = None
    announcementTitle: str | None = None
    announcementTime: int | None = None
    adjunctUrl: str | None = None
    adjunctSize: int | None = None
    adjunctType: str | None = None
    storageTime: str | None = None
    columnId: str | None = None
    pageColumn: str | None = None
    announcementType: str | None = None
    associateAnnouncement: str | None = None
    important: str | None = None
    batchNum: str | None = None
    announcementContent: str | None = None
    orgName: str | None = None
    tileSecName: str | None = None
    shortTitle: str | None = None
    announcementTypeName: str | None = None
    secNameList: list[Any] | None = None
