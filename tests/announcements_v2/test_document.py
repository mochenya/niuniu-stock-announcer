"""公告 Document Service 的离线路由、路径和 PDF 结构测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pymupdf
import pytest

from niuniu_stock_announcer.announcements.document import (
    AnnouncementDocumentService,
    DocumentValidationError,
)
from niuniu_stock_announcer.announcements.schema import ChinaAnnouncement

SOURCE_URLS = {
    "cninfo": "https://static.cninfo.com.cn/finalpage/example.PDF",
    "sse": "https://static.sse.com.cn/disclosure/example.pdf",
    "szse": "https://disc.static.szse.cn/download/disc/example.PDF",
}


class _FakeProvider:
    def __init__(self, provider_key: str) -> None:
        self.provider_key = provider_key
        self.download_calls: list[Path] = []

    def query(self, _query):  # pragma: no cover - 本测试只验证 document capability
        raise AssertionError("document 测试不应查询 Provider")

    def download_pdf(
        self, _announcement: ChinaAnnouncement, *, target_path: Path
    ) -> Path:
        self.download_calls.append(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with pymupdf.open() as document:
            document.new_page()
            document.save(target_path)
        return target_path

    def close(self) -> None:
        return None


class _WrongPathProvider(_FakeProvider):
    def download_pdf(
        self, announcement: ChinaAnnouncement, *, target_path: Path
    ) -> Path:
        super().download_pdf(announcement, target_path=target_path)
        wrong_path = target_path.parent / "unexpected.pdf"
        target_path.replace(wrong_path)
        return wrong_path


class _PartialFailureProvider(_FakeProvider):
    def download_pdf(
        self, _announcement: ChinaAnnouncement, *, target_path: Path
    ) -> Path:
        self.download_calls.append(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"%PDF-partial")
        raise ConnectionError("下载中断")


class _SymlinkEscapeProvider(_FakeProvider):
    def __init__(self, provider_key: str, outside_pdf: Path) -> None:
        super().__init__(provider_key)
        self._outside_pdf = outside_pdf

    def download_pdf(
        self, _announcement: ChinaAnnouncement, *, target_path: Path
    ) -> Path:
        self.download_calls.append(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.symlink_to(self._outside_pdf)
        return target_path


class _ParentSymlinkEscapeProvider(_FakeProvider):
    def __init__(self, provider_key: str, outside_dir: Path) -> None:
        super().__init__(provider_key)
        self._outside_dir = outside_dir

    def download_pdf(
        self, _announcement: ChinaAnnouncement, *, target_path: Path
    ) -> Path:
        self.download_calls.append(target_path)
        target_path.parent.parent.mkdir(parents=True, exist_ok=True)
        self._outside_dir.mkdir(parents=True, exist_ok=True)
        target_path.parent.symlink_to(self._outside_dir, target_is_directory=True)
        with pymupdf.open() as document:
            document.new_page()
            document.save(self._outside_dir / target_path.name)
        return target_path


def _announcement(provider_key: str) -> ChinaAnnouncement:
    return ChinaAnnouncement(
        provider_key=provider_key,
        provider_announcement_id="announcement-1",
        market_scope="a_share",
        title="测试公告",
        published_at=datetime(2026, 8, 12, 1, tzinfo=UTC),
        source_url=SOURCE_URLS[provider_key],
    )


@pytest.mark.parametrize("provider_key", ["cninfo", "sse", "szse"])
def test_document_download_is_validated_and_reused_inside_storage_root(
    tmp_path: Path, provider_key: str
) -> None:
    provider = _FakeProvider(provider_key)
    service = AnnouncementDocumentService(tmp_path, {provider_key: provider})

    first = service.ensure_pdf(_announcement(provider_key))
    second = service.ensure_pdf(_announcement(provider_key))

    assert first == second
    assert first.provider_key == provider_key
    assert first.storage_relative_path.startswith(f"{provider_key}/2026/08/")
    assert first.local_path.is_relative_to(tmp_path.resolve())
    assert first.local_path.read_bytes().startswith(b"%PDF-")
    assert first.size_bytes == first.local_path.stat().st_size
    assert len(first.sha256) == 64
    assert first.page_count == 1
    assert provider.download_calls == [first.local_path]


def test_document_rejects_source_host_before_calling_provider(tmp_path: Path) -> None:
    provider = _FakeProvider("cninfo")
    service = AnnouncementDocumentService(tmp_path, {"cninfo": provider})
    announcement = _announcement("cninfo").model_copy(
        update={"source_url": "https://static.sse.com.cn/wrong.pdf"}
    )

    with pytest.raises(DocumentValidationError, match="来源域名"):
        service.ensure_pdf(announcement)

    assert provider.download_calls == []


def test_document_replaces_corrupt_cache_at_same_stable_path(tmp_path: Path) -> None:
    provider = _FakeProvider("cninfo")
    service = AnnouncementDocumentService(tmp_path, {"cninfo": provider})
    first = service.ensure_pdf(_announcement("cninfo"))
    first.local_path.write_bytes(b"not a pdf")

    repaired = service.ensure_pdf(_announcement("cninfo"))

    assert repaired.local_path == first.local_path
    assert repaired.page_count == 1
    assert len(provider.download_calls) == 2


def test_document_rejects_downloader_path_escape_or_rename(tmp_path: Path) -> None:
    provider = _WrongPathProvider("cninfo")
    service = AnnouncementDocumentService(tmp_path, {"cninfo": provider})

    with pytest.raises(DocumentValidationError, match="非预期 storage 路径"):
        service.ensure_pdf(_announcement("cninfo"))


def test_document_removes_partial_file_when_downloader_fails(tmp_path: Path) -> None:
    provider = _PartialFailureProvider("cninfo")
    service = AnnouncementDocumentService(tmp_path, {"cninfo": provider})

    with pytest.raises(ConnectionError, match="下载中断"):
        service.ensure_pdf(_announcement("cninfo"))

    assert provider.download_calls
    assert not provider.download_calls[0].exists()


def test_document_revalidates_storage_boundary_after_download(tmp_path: Path) -> None:
    outside_pdf = tmp_path.parent / f"{tmp_path.name}-outside.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.save(outside_pdf)
    provider = _SymlinkEscapeProvider("cninfo", outside_pdf)
    service = AnnouncementDocumentService(tmp_path, {"cninfo": provider})

    with pytest.raises(DocumentValidationError, match="越出 storage root"):
        service.ensure_pdf(_announcement("cninfo"))

    assert provider.download_calls
    assert not provider.download_calls[0].exists()
    assert outside_pdf.is_file()


def test_document_cleanup_never_follows_parent_symlink_outside_root(
    tmp_path: Path,
) -> None:
    outside_dir = tmp_path.parent / f"{tmp_path.name}-outside"
    provider = _ParentSymlinkEscapeProvider("cninfo", outside_dir)
    service = AnnouncementDocumentService(tmp_path, {"cninfo": provider})

    with pytest.raises(DocumentValidationError, match="越出 storage root"):
        service.ensure_pdf(_announcement("cninfo"))

    assert provider.download_calls
    assert (outside_dir / provider.download_calls[0].name).is_file()
