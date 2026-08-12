"""公告 Provider Service 的窄公共协议。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from niuniu_stock_announcer.announcements.schema import (
    AnnouncementQuery,
    ChinaAnnouncement,
    ProviderKey,
    ProviderQueryResult,
)


class AnnouncementProviderService(Protocol):
    """约束查询与 PDF 下载所需的最小 Provider 能力。"""

    @property
    def provider_key(self) -> ProviderKey:
        """返回当前 service 的稳定 Provider key。"""
        ...

    def query(self, query: AnnouncementQuery) -> ProviderQueryResult:
        """执行一次显式查询并返回 provider-neutral 公告。"""
        ...

    def download_pdf(
        self, announcement: ChinaAnnouncement, *, target_path: Path
    ) -> Path:
        """把公告 PDF 下载到调用方指定的稳定路径。"""
        ...

    def close(self) -> None:
        """关闭按需创建的第三方 client。"""
        ...
