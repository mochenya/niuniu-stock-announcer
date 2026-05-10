from __future__ import annotations

from contextlib import contextmanager

from domain.common import AnnouncementSource, Market

# 第一版保持显式路由：每个市场只对应一个公告源。
MARKET_ANNOUNCEMENT_SOURCE_MAP: dict[Market, AnnouncementSource] = {
    "hk": "cninfo",
    "bj": "cninfo",
    "sh": "sse",
    "sz": "szse",
}

SUPPORTED_ANNOUNCEMENT_SOURCES = frozenset[AnnouncementSource](
    {"cninfo", "sse", "szse"}
)


def announcement_source_for_market(market: Market) -> AnnouncementSource:
    """根据当前固定路由，把市场转换为公告源。"""
    return MARKET_ANNOUNCEMENT_SOURCE_MAP[market]


def normalize_announcement_source(
    value: AnnouncementSource | str | object,
) -> AnnouncementSource:
    """把第三方模型或字符串中的来源值规范化成内部 Literal。"""
    raw_value = getattr(value, "value", value)
    normalized = str(raw_value).strip().lower()
    if normalized not in SUPPORTED_ANNOUNCEMENT_SOURCES:
        raise ValueError(f"unsupported announcement source: {value}")
    return normalized  # type: ignore[return-value]


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
