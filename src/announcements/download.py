from __future__ import annotations

from pathlib import Path

from cninfo_announcement.models import BusinessAnnouncement
from cninfo_announcement.pdf import download_pdf as download_cninfo_pdf
from sse_announcement.pdf import download_pdf as download_sse_pdf
from szse_announcement.pdf import download_pdf as download_szse_pdf

from announcements.sources import (
    normalize_announcement_source,
)
from domain.common import AnnouncementSource


def download_announcement_pdf(
    announcement: BusinessAnnouncement,
    *,
    save_dir: str | Path | None = None,
) -> Path:
    """按公告来源调用对应下载器，并返回本地 PDF 路径。"""
    source = normalize_announcement_source(announcement.source)
    if source == "cninfo":
        return download_cninfo_pdf(
            announcement,
            save_dir=_source_save_dir(save_dir, source=source),
        )
    if source == "sse":
        return download_sse_pdf(
            announcement,
            save_dir=_source_save_dir(save_dir, source=source),
        )
    if source == "szse":
        return download_szse_pdf(
            announcement,
            save_dir=_source_save_dir(save_dir, source=source),
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
