"""真实网络测试的双重显式门。"""

from __future__ import annotations

import os

import pytest

REQUIRED_LIVE_NODE_IDS = frozenset(
    {
        "tests/live/test_provider_queries.py::test_cninfo_market_route_contract[cninfo-sh]",
        "tests/live/test_provider_queries.py::test_cninfo_market_route_contract[cninfo-sz]",
        "tests/live/test_provider_queries.py::test_cninfo_market_route_contract[cninfo-bj]",
        "tests/live/test_provider_queries.py::test_cninfo_market_route_contract[cninfo-hk]",
        "tests/live/test_provider_queries.py::test_sse_keyword_route_contract",
        "tests/live/test_provider_queries.py::test_szse_stock_route_contract",
        "tests/live/test_provider_queries.py::test_cninfo_pdf_contract",
        "tests/live/test_provider_queries.py::test_sse_pdf_contract",
        "tests/live/test_provider_queries.py::test_szse_pdf_contract",
    }
)
LIVE_CASE_RESULTS: dict[str, str] = {}


@pytest.fixture
def record_live_contract(request: pytest.FixtureRequest):
    """记录单个 live case 的脱敏合同结果，供会话末尾审计。

    Args:
        request: 当前测试节点，用于把结果关联到完整 nodeid。

    Returns:
        接受一行脱敏结果摘要的回调。
    """

    def record(detail: str) -> None:
        LIVE_CASE_RESULTS[request.node.nodeid] = detail

    return record


@pytest.fixture(autouse=True)
def require_explicit_live_gate(request: pytest.FixtureRequest) -> None:
    """要求环境开关与 pytest marker 选择同时启用 live suite。

    Args:
        request: 当前 pytest 配置和测试节点。
    """
    if os.environ.get("NIUNIU_RUN_LIVE_TESTS") != "1":
        pytest.skip("需要设置 NIUNIU_RUN_LIVE_TESTS=1")
    if not _live_marker_selected(request.config):
        pytest.skip("需要显式使用 -m live")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """启用完整 live suite 时拒绝关键 case 缺失或被跳过。

    Args:
        session: pytest session，用于读取已收集节点和终端报告。
        exitstatus: pytest 已计算的退出码；这里只在原结果成功时补充完整性失败。
    """
    if not _live_gate_enabled(session.config) or exitstatus != pytest.ExitCode.OK:
        return
    collected = {item.nodeid for item in session.items}
    missing = sorted(REQUIRED_LIVE_NODE_IDS - collected)
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    unexpected_outcomes: list[str] = []
    if reporter is not None:
        for category in ("skipped", "xfailed", "xpassed"):
            unexpected_outcomes.extend(
                report.nodeid for report in reporter.stats.get(category, [])
            )
    unreported = sorted(REQUIRED_LIVE_NODE_IDS - LIVE_CASE_RESULTS.keys())
    if missing or unexpected_outcomes or unreported:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    if reporter is not None:
        if session.exitstatus == pytest.ExitCode.OK:
            reporter.write_sep("=", "live suite 合同结果")
            for nodeid in sorted(REQUIRED_LIVE_NODE_IDS):
                reporter.write_line(f"{nodeid}: {LIVE_CASE_RESULTS[nodeid]}")
        else:
            reporter.write_sep("=", "live suite 完整性失败")
            if missing:
                reporter.write_line("缺少关键用例: " + ", ".join(missing))
            if unexpected_outcomes:
                reporter.write_line(
                    "出现 skip/xfail/xpass: " + ", ".join(unexpected_outcomes)
                )
            if unreported:
                reporter.write_line("缺少合同结果: " + ", ".join(unreported))


def _live_gate_enabled(config: pytest.Config) -> bool:
    return os.environ.get("NIUNIU_RUN_LIVE_TESTS") == "1" and _live_marker_selected(
        config
    )


def _live_marker_selected(config: pytest.Config) -> bool:
    return (config.getoption("-m") or "").strip() == "live"
