"""SZSE 公告查询与下载 Service。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from announcement_common.models import AnnouncementSource, BusinessAnnouncement

from niuniu_stock_announcer.announcements.providers.szse.mapper import (
    map_szse_announcement,
)
from niuniu_stock_announcer.announcements.schema import (
    AnnouncementQuery,
    ChinaAnnouncement,
    ProviderItemError,
    ProviderKey,
    ProviderQueryResult,
)


class _SzseClient(Protocol):
    def query_announcements(self, **kwargs: object) -> Any: ...

    def download_pdf(
        self, announcement: BusinessAnnouncement, *, save_dir: Path
    ) -> Path: ...

    def close(self) -> None: ...


def _create_client() -> _SzseClient:
    from szse_announcement.client import SZSEAnnouncementClient

    return SZSEAnnouncementClient()


class SzseAnnouncementService:
    """复用一个按需 SZSE client 执行查询和 PDF 下载。"""

    def __init__(
        self, client_factory: Callable[[], _SzseClient] = _create_client
    ) -> None:
        """保存 client factory，构造阶段不发起网络连接。

        Args:
            client_factory: 首次真实操作时调用的可注入 client factory。
        """
        self._client_factory = client_factory
        self._client: _SzseClient | None = None

    @property
    def provider_key(self) -> ProviderKey:
        """返回 SZSE Provider key。"""
        return "szse"

    def query(self, query: AnnouncementQuery) -> ProviderQueryResult:
        """执行 SZSE 查询并逐条隔离确定的 mapper 失败。

        Args:
            query: 已校验且 exchange 必须为 `sz` 的查询。

        Returns:
            成功映射的公告、单条错误与截断标记。
        """
        if query.exchange != "sz":
            raise ValueError("SZSE 只支持 exchange=sz")
        result = self._require_client().query_announcements(
            stock=query.stock_code,
            searchkey=query.search_keyword,
            start_date=query.start_date,
            end_date=query.end_date,
            limit=query.limit,
        )
        mapped = []
        errors = []
        for index, item in enumerate(result.response.announcements):
            try:
                mapped.append(map_szse_announcement(item, query))
            except ValueError as exc:
                errors.append(
                    ProviderItemError(
                        item_index=index,
                        error_type=exc.__class__.__name__,
                        message=str(exc),
                    )
                )
        return ProviderQueryResult(
            provider_key="szse",
            items=tuple(mapped),
            item_errors=tuple(errors),
            has_more=bool(result.response.has_more),
        )

    def download_pdf(
        self, announcement: ChinaAnnouncement, *, target_path: Path
    ) -> Path:
        """使用 SZSE 会话把 PDF 下载到稳定目标路径。

        Args:
            announcement: 已由当前 Provider 映射的业务公告。
            target_path: Document Service 预先验证的绝对目标路径。

        Returns:
            SDK 实际写入的 PDF 路径。
        """
        if announcement.provider_key != "szse":
            raise ValueError("SZSE service 不能下载其他 Provider 公告")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        sdk_announcement = BusinessAnnouncement(
            source=AnnouncementSource.SZSE,
            announcement_id=target_path.stem,
            adjunct_url=announcement.source_url,
        )
        return self._require_client().download_pdf(
            sdk_announcement, save_dir=target_path.parent
        )

    def close(self) -> None:
        """关闭已创建的 SZSE client；尚未使用时保持无副作用。"""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> SzseAnnouncementService:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _require_client(self) -> _SzseClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client
