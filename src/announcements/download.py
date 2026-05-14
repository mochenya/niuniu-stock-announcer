from __future__ import annotations

from typing import Protocol
from pathlib import Path

from cninfo_announcement.models import BusinessAnnouncement
from cninfo_announcement.pdf import download_pdf as download_cninfo_pdf
from sse_announcement.pdf import download_pdf as download_sse_pdf
from szse_announcement.pdf import download_pdf as download_szse_pdf

from announcements.sources import (
    normalize_announcement_source,
)
from domain.common import AnnouncementSource


class AnnouncementPdfClient(Protocol):
    def download_pdf(
        self,
        announcement: BusinessAnnouncement,
        *,
        save_dir: str | Path | None = None,
    ) -> Path: ...


def download_announcement_pdf(
    announcement: BusinessAnnouncement,
    *,
    save_dir: str | Path | None = None,
    client: AnnouncementPdfClient | None = None,
) -> Path:
    """按公告来源调用对应下载器，并返回本地 PDF 路径。"""
    source = normalize_announcement_source(announcement.source)
    source_save_dir = _source_save_dir(save_dir, source=source)
    if client is not None:
        # 摘要阶段会按公告源复用 client，保留 cookie/连接并减少重复 TLS 和挑战请求。
        return client.download_pdf(announcement, save_dir=source_save_dir)
    if source == "cninfo":
        return download_cninfo_pdf(
            announcement,
            save_dir=source_save_dir,
        )
    if source == "sse":
        return download_sse_pdf(
            announcement,
            save_dir=source_save_dir,
        )
    if source == "szse":
        return download_szse_pdf(
            announcement,
            save_dir=source_save_dir,
        )
    raise ValueError(f"unsupported announcement source: {source}")


def _source_save_dir(
    save_dir: str | Path | None,
    *,
    source: AnnouncementSource,
) -> Path | None:
    if save_dir is None:
        return None
    # 按来源分目录保存，避免不同公告源生成的文件名互相覆盖。
    return Path(save_dir) / source
