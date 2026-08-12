"""公告 PDF 的来源路由、存储路径与结构校验。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pymupdf

from niuniu_stock_announcer.announcements.schema import (
    ChinaAnnouncement,
    ProviderKey,
    StoredAnnouncementDocument,
)
from niuniu_stock_announcer.announcements.service import AnnouncementProviderService
from niuniu_stock_announcer.storage.document import resolve_storage_path

PDF_MAGIC = b"%PDF-"
PDF_EOF = b"%%EOF"
PDF_EOF_SEARCH_BYTES = 2048
MIN_PDF_SIZE_BYTES = 256
SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
PROVIDER_PDF_HOSTS: dict[ProviderKey, frozenset[str]] = {
    "cninfo": frozenset({"static.cninfo.com.cn"}),
    "sse": frozenset({"static.sse.com.cn"}),
    "szse": frozenset({"disc.static.szse.cn"}),
}


class DocumentValidationError(ValueError):
    """表示下载结果不满足可持久化 PDF 合同。"""


class AnnouncementDocumentService:
    """在 storage root 内下载、复用并验证公告 PDF。"""

    def __init__(
        self,
        storage_root: Path,
        provider_services: Mapping[ProviderKey, AnnouncementProviderService],
    ) -> None:
        """绑定显式 storage root 与 Provider service registry。

        Args:
            storage_root: 所有 document 相对路径解析的唯一根目录。
            provider_services: 由 composition root 注入的 Provider services。
        """
        self._storage_root = storage_root.resolve()
        self._provider_services = dict(provider_services)

    def ensure_pdf(self, announcement: ChinaAnnouncement) -> StoredAnnouncementDocument:
        """复用有效本地文件，或下载并验证一份公告 PDF。

        Args:
            announcement: 带稳定 Provider 身份和来源 URL 的业务公告。

        Returns:
            包含相对路径、大小、哈希和页数的冻结 document 快照。

        Raises:
            DocumentValidationError: 来源路由、落盘路径或 PDF 结构不符合合同。
            KeyError: composition root 未注册公告对应的 Provider service。
        """
        _validate_source_route(announcement.provider_key, announcement.source_url)
        relative_path = _build_storage_relative_path(announcement)
        target_path = resolve_storage_path(self._storage_root, relative_path)
        if target_path.is_file():
            try:
                return _validate_pdf(announcement, relative_path, target_path)
            except DocumentValidationError:
                # 损坏缓存不能继续被摘要/投递复用；删除后让受控下载重新建立同一稳定路径。
                _remove_target_if_inside_storage(self._storage_root, target_path)

        provider = self._provider_services[announcement.provider_key]
        if provider.provider_key != announcement.provider_key:
            raise DocumentValidationError("Provider registry key 与 service 身份不一致")
        try:
            reported_path = provider.download_pdf(
                announcement,
                target_path=target_path,
            )
        except Exception:
            # 下载器可能在网络异常前写入半文件；失败结果不能进入后续缓存复用。
            _remove_target_if_inside_storage(self._storage_root, target_path)
            raise
        try:
            # 下载期间目录或目标可能被替换为符号链接，必须重新从相对路径验证边界。
            verified_target_path = resolve_storage_path(
                self._storage_root, relative_path
            )
        except ValueError as exc:
            _remove_target_if_inside_storage(self._storage_root, target_path)
            raise DocumentValidationError(
                "Provider downloader 写入后路径越出 storage root"
            ) from exc
        if reported_path.resolve() != verified_target_path:
            _remove_target_if_inside_storage(self._storage_root, target_path)
            raise DocumentValidationError(
                "Provider downloader 写入了非预期 storage 路径"
            )
        try:
            return _validate_pdf(announcement, relative_path, verified_target_path)
        except DocumentValidationError:
            _remove_target_if_inside_storage(self._storage_root, target_path)
            raise


def _build_storage_relative_path(announcement: ChinaAnnouncement) -> str:
    local_time = announcement.published_at.astimezone(SHANGHAI_TIMEZONE)
    identity_digest = hashlib.sha256(
        f"{announcement.provider_key}:{announcement.provider_announcement_id}".encode()
    ).hexdigest()
    return (
        f"{announcement.provider_key}/{local_time:%Y}/{local_time:%m}/"
        f"{identity_digest}.pdf"
    )


def _validate_source_route(provider_key: ProviderKey, source_url: str) -> None:
    parsed = urlsplit(source_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in PROVIDER_PDF_HOSTS[provider_key]:
        raise DocumentValidationError(
            f"{provider_key} source_url 不属于已审阅的 PDF 来源域名"
        )


def _validate_pdf(
    announcement: ChinaAnnouncement, relative_path: str, path: Path
) -> StoredAnnouncementDocument:
    resolved_path = path.resolve()
    try:
        size_bytes = resolved_path.stat().st_size
        if size_bytes < MIN_PDF_SIZE_BYTES:
            raise DocumentValidationError("PDF 文件小于合理最小大小")
        with resolved_path.open("rb") as file:
            if file.read(len(PDF_MAGIC)) != PDF_MAGIC:
                raise DocumentValidationError("文件缺少 %PDF- 签名")
            file.seek(max(0, size_bytes - PDF_EOF_SEARCH_BYTES))
            if PDF_EOF not in file.read():
                raise DocumentValidationError("PDF 文件缺少结束标记")
        with pymupdf.open(resolved_path) as document:
            if document.needs_pass:
                raise DocumentValidationError("PDF 需要密码，无法进入摘要流程")
            page_count = document.page_count
        if page_count <= 0:
            raise DocumentValidationError("PDF 不包含可读取页面")
        sha256 = _sha256_file(resolved_path)
    except DocumentValidationError:
        raise
    except (OSError, ValueError, pymupdf.FileDataError) as exc:
        raise DocumentValidationError("PDF 文件无法读取或解析") from exc
    return StoredAnnouncementDocument(
        provider_key=announcement.provider_key,
        provider_announcement_id=announcement.provider_announcement_id,
        source_url=announcement.source_url,
        storage_relative_path=relative_path,
        local_path=resolved_path,
        size_bytes=size_bytes,
        sha256=sha256,
        page_count=page_count,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_target_if_inside_storage(storage_root: Path, target_path: Path) -> None:
    """仅在目标父目录仍位于 storage root 内时删除文件或最终符号链接。

    下载期间父目录可能被替换为指向 root 外的符号链接；此时沿原路径删除会触及
    外部文件，因此宁可保留未知产物供诊断，也不能越界清理。

    Args:
        storage_root: 已解析的 document storage 根目录。
        target_path: 本次下载约定的目标路径。
    """
    try:
        resolved_parent = target_path.parent.resolve()
    except OSError:
        return
    if not resolved_parent.is_relative_to(storage_root):
        return
    target_path.unlink(missing_ok=True)
