"""China discovery task 编译与 Provider resolver 测试。"""

from __future__ import annotations

from datetime import date

import pytest

from niuniu_stock_announcer.pipelines.china.discovery.market_keywords import (
    compile_market_keyword_tasks,
)
from niuniu_stock_announcer.pipelines.china.discovery.selected_stocks import (
    compile_selected_stock_tasks,
)
from niuniu_stock_announcer.pipelines.china.profile import ChinaMarketProfile
from niuniu_stock_announcer.pipelines.china.provider_resolver import (
    ChinaProviderResolver,
)
from niuniu_stock_announcer.pipelines.china.schema import (
    AnnouncementProviderRoutes,
    MarketKeywordsPlan,
    SelectedStocksPlan,
)


class _FakeProvider:
    def __init__(self, provider_key: str, *, error: Exception | None = None) -> None:
        self.provider_key = provider_key
        self.error = error
        self.queries = []

    def query(self, query):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return object()

    def download_pdf(self, _announcement, *, target_path):
        return target_path

    def close(self) -> None:
        return None


def test_selected_strategy_only_compiles_stock_code_queries() -> None:
    plan = SelectedStocksPlan.model_validate(
        {
            "market": "china",
            "plan_type": "selected_stocks",
            "plan_key": "selected-stocks",
            "window_days": 3,
            "announcement_providers": {"sh": "sse", "sz": "szse"},
            "market_scopes": {
                "a_share": {
                    "stocks": [
                        {"exchange": "sh", "stock_code": "688090"},
                        {"exchange": "sz", "stock_code": "000510"},
                    ],
                    "filters": {"title_exclude_keywords": ["更正"]},
                }
            },
        }
    )

    tasks = compile_selected_stock_tasks(plan, end_date=date(2026, 8, 12), limit=5)

    assert [(task.provider_key, task.query.stock_code) for task in tasks] == [
        ("sse", "688090"),
        ("szse", "000510"),
    ]
    assert all(task.query.search_keyword is None for task in tasks)
    assert all(task.query.start_date == date(2026, 8, 10) for task in tasks)
    assert all(task.title_exclude_keywords == ("更正",) for task in tasks)


def test_keyword_strategy_expands_scope_exchanges_and_only_uses_keywords() -> None:
    plan = MarketKeywordsPlan.model_validate(
        {
            "market": "china",
            "plan_type": "market_keywords",
            "plan_key": "market-keywords",
            "window_days": 2,
            "announcement_providers": {"sh": "sse", "sz": "szse"},
            "market_scopes": {
                "a_share": {
                    "discovery": {"search_keywords": ["回购", "中标"]},
                    "filters": {"title_exclude_keywords": ["更正"]},
                },
                "hk": {"discovery": {"search_keywords": ["盈利警告"]}},
            },
        }
    )

    tasks = compile_market_keyword_tasks(
        plan,
        end_date=date(2026, 8, 12),
        profile=ChinaMarketProfile(),
        limit=2,
    )

    assert [
        (
            task.market_scope,
            task.query.exchange,
            task.provider_key,
            task.query.search_keyword,
        )
        for task in tasks
    ] == [
        ("a_share", "sh", "sse", "回购"),
        ("a_share", "sh", "sse", "中标"),
        ("a_share", "sz", "szse", "回购"),
        ("a_share", "sz", "szse", "中标"),
        ("a_share", "bj", "cninfo", "回购"),
        ("a_share", "bj", "cninfo", "中标"),
        ("hk", "hk", "cninfo", "盈利警告"),
    ]
    assert all(task.query.stock_code is None for task in tasks)
    assert tasks[0].title_exclude_keywords == ("更正",)
    assert tasks[-1].title_exclude_keywords == ()


def test_resolver_defaults_to_cninfo_and_does_not_fallback_explicit_failure() -> None:
    cninfo = _FakeProvider("cninfo")
    failure = RuntimeError("SSE unavailable")
    sse = _FakeProvider("sse", error=failure)
    resolver = ChinaProviderResolver(
        AnnouncementProviderRoutes(sh="sse"),
        {"cninfo": cninfo, "sse": sse},
    )

    assert resolver.provider_key_for("bj") == "cninfo"
    with pytest.raises(RuntimeError, match="SSE unavailable"):
        resolver.resolve("sh").query(object())

    assert len(sse.queries) == 1
    assert cninfo.queries == []


def test_resolver_rejects_registry_identity_mismatch() -> None:
    resolver = ChinaProviderResolver(
        AnnouncementProviderRoutes(), {"cninfo": _FakeProvider("sse")}
    )

    with pytest.raises(ValueError, match="registry key"):
        resolver.resolve("hk")
