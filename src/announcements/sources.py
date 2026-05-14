from __future__ import annotations

from contextlib import contextmanager

from domain.common import (
    AnnouncementSource,
    DEFAULT_ANNOUNCEMENT_SOURCE_BY_MARKET,
    Market,
    normalize_announcement_source as normalize_common_announcement_source,
)


def announcement_source_for_market(market: Market) -> AnnouncementSource:
    """按默认路由把市场转换为公告源。"""
    return DEFAULT_ANNOUNCEMENT_SOURCE_BY_MARKET[market]


def normalize_announcement_source(
    value: AnnouncementSource | str | object,
) -> AnnouncementSource:
    return normalize_common_announcement_source(value)


@contextmanager
def create_announcement_client(source: AnnouncementSource):
    """只为当前公告源按需创建第三方客户端。

    延迟 import 让未使用的交易所 SDK 不影响其他市场的同步任务启动。
    """
    if source == "cninfo":
        from cninfo_announcement.client import CNInfoClient

        with CNInfoClient() as client:
            yield client
        return
    if source == "sse":
        from sse_announcement.client import SSEAnnouncementClient

        with SSEAnnouncementClient() as client:
            yield client
        return
    if source == "szse":
        from szse_announcement.client import SZSEAnnouncementClient

        with SZSEAnnouncementClient() as client:
            yield client
        return
    raise ValueError(f"unsupported announcement source: {source}")
