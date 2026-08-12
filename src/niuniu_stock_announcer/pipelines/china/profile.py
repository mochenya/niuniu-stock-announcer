"""China Pipeline 内稳定的 scope 与 exchange 规则。"""

from __future__ import annotations

from niuniu_stock_announcer.pipelines.china.schema import Exchange, MarketScope

SCOPE_EXCHANGE_ORDER: dict[MarketScope, tuple[Exchange, ...]] = {
    "a_share": ("sh", "sz", "bj"),
    "hk": ("hk",),
}


class ChinaMarketProfile:
    """集中提供 China scope/exchange 的稳定映射。"""

    def exchanges_for_scope(self, scope: MarketScope) -> tuple[Exchange, ...]:
        """返回一个 market scope 的固定 exchange 顺序。

        Args:
            scope: `a_share` 或 `hk` 业务范围。

        Returns:
            该范围内需要 discovery 的 exchange 元组。
        """
        return SCOPE_EXCHANGE_ORDER[scope]

    def scope_for_exchange(self, exchange: Exchange) -> MarketScope:
        """返回 exchange 唯一所属的 market scope。

        Args:
            exchange: China Pipeline 支持的交易所标识。

        Returns:
            `a_share` 或 `hk`。
        """
        return "hk" if exchange == "hk" else "a_share"
