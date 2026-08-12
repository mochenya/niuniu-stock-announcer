"""按 China Plan 路由解析已注入的公告 Provider service。"""

from __future__ import annotations

from collections.abc import Mapping

from niuniu_stock_announcer.announcements.schema import ProviderKey
from niuniu_stock_announcer.announcements.service import AnnouncementProviderService
from niuniu_stock_announcer.pipelines.china.schema import (
    AnnouncementProviderRoutes,
    Exchange,
)


class ChinaProviderResolver:
    """只执行显式 Plan 路由，不实现任何 Provider fallback。"""

    def __init__(
        self,
        routes: AnnouncementProviderRoutes,
        services: Mapping[ProviderKey, AnnouncementProviderService],
    ) -> None:
        """绑定 Plan 根路由和由 composition root 注入的 services。

        Args:
            routes: 已通过合法 exchange/provider 矩阵校验的 Plan 路由。
            services: 以稳定 Provider key 索引的 service registry。
        """
        self._routes = routes
        self._services = dict(services)

    def provider_key_for(self, exchange: Exchange) -> ProviderKey:
        """返回 Plan 对指定 exchange 选择的 Provider key。

        Args:
            exchange: 当前 discovery 查询的 exchange。

        Returns:
            Plan 根 mapping 中的显式值或已解析默认值。
        """
        return getattr(self._routes, exchange)

    def resolve(self, exchange: Exchange) -> AnnouncementProviderService:
        """取得唯一选定 service，缺失或错配时直接失败。

        Args:
            exchange: 当前 discovery 查询的 exchange。

        Returns:
            与 Plan 路由身份一致的 Provider service。

        Raises:
            KeyError: composition root 没有注册选定 Provider。
            ValueError: registry key 与 service 自报身份不一致。
        """
        provider_key = self.provider_key_for(exchange)
        service = self._services[provider_key]
        if service.provider_key != provider_key:
            raise ValueError("Provider registry key 与 service 身份不一致")
        # 显式 SSE/SZSE 失败会从该 service 原样冒泡；此 resolver 从不尝试 CNInfo。
        return service
