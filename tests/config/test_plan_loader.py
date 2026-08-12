"""China Plan 与 AppSettings 配置契约测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from niuniu_stock_announcer.config.plan_loader import PlanLoadError, load_china_plan
from niuniu_stock_announcer.config.settings import load_app_settings
from niuniu_stock_announcer.pipelines.china.schema import (
    MarketKeywordsPlan,
    SelectedStocksPlan,
)

SELECTED_PLAN = """\
market: china
plan_type: selected_stocks
plan_key: china-selected-stocks
window_days: 2
announcement_providers:
  sh: sse
  sz: szse
market_scopes:
  a_share:
    stocks:
      - exchange: sh
        stock_code: "688090"
        name: 瑞松科技
      - exchange: sz
        stock_code: "000510"
    filters:
      title_exclude_keywords: [业绩说明会, 业绩说明会]
    delivery:
      telegram:
        target_key: selected-a-share
        target_url: ${TELEGRAM_TARGET}
  hk:
    stocks:
      - exchange: hk
        stock_code: "06869"
"""

KEYWORD_PLAN = """\
market: china
plan_type: market_keywords
plan_key: china-market-keywords
window_days: 3
market_scopes:
  a_share:
    discovery:
      search_keywords: [回购, 中标, 回购]
    filters:
      title_exclude_keywords: [更正公告]
  hk:
    discovery:
      search_keywords: [盈利警告]
"""


def _write_plan(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "plan.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_selected_plan_resolves_env_and_defaults_cninfo(tmp_path: Path) -> None:
    plan = load_china_plan(
        _write_plan(tmp_path, SELECTED_PLAN),
        environ={"TELEGRAM_TARGET": "https://t.me/c/123/9"},
    )

    assert isinstance(plan, SelectedStocksPlan)
    assert plan.announcement_providers.sh == "sse"
    assert plan.announcement_providers.sz == "szse"
    assert plan.announcement_providers.bj == "cninfo"
    assert plan.announcement_providers.hk == "cninfo"
    assert plan.market_scopes["a_share"].filters.title_exclude_keywords == (
        "业绩说明会",
    )
    assert (
        plan.market_scopes["a_share"].delivery.telegram.target_url
        == "https://t.me/c/123/9"
    )


def test_load_keyword_plan_uses_independent_schema(tmp_path: Path) -> None:
    plan = load_china_plan(_write_plan(tmp_path, KEYWORD_PLAN), environ={})

    assert isinstance(plan, MarketKeywordsPlan)
    assert plan.market_scopes["a_share"].discovery.search_keywords == (
        "回购",
        "中标",
    )
    assert plan.market_scopes["hk"].discovery.search_keywords == ("盈利警告",)


def test_process_environment_overrides_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_TARGET=https://t.me/c/1/2\n", encoding="utf-8")

    plan = load_china_plan(
        _write_plan(tmp_path, SELECTED_PLAN),
        env_file=env_file,
        environ={"TELEGRAM_TARGET": "https://t.me/c/8/9"},
    )

    assert (
        plan.market_scopes["a_share"].delivery.telegram.target_url
        == "https://t.me/c/8/9"
    )


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (SELECTED_PLAN.replace("window_days: 2", "window_days: 0"), "window_days"),
        (SELECTED_PLAN.replace("  sh: sse", "  sh: szse"), "只能使用"),
        (
            SELECTED_PLAN.replace("market_scopes:", "market_scopes: {}\nignored:"),
            "Extra",
        ),
        (
            SELECTED_PLAN.replace(
                "        name: 瑞松科技",
                "        name: 瑞松科技\n        keywords: [回购]",
            ),
            "keywords",
        ),
        (
            SELECTED_PLAN.replace(
                '      - exchange: sz\n        stock_code: "000510"',
                '      - exchange: sh\n        stock_code: "688090"',
            ),
            "重复配置",
        ),
        (
            SELECTED_PLAN.replace(
                '      - exchange: hk\n        stock_code: "06869"',
                '      - exchange: sh\n        stock_code: "600000"',
            ),
            "不接受 exchange=sh",
        ),
        (KEYWORD_PLAN.replace("[盈利警告]", "[]"), "search_keywords"),
        (
            KEYWORD_PLAN.replace("market_scopes:", "market_scopes: {}\nignored:"),
            "Extra",
        ),
    ],
)
def test_invalid_plan_contracts_fail_before_use(
    tmp_path: Path, content: str, message: str
) -> None:
    with pytest.raises(PlanLoadError, match=message):
        load_china_plan(
            _write_plan(tmp_path, content),
            environ={"TELEGRAM_TARGET": "https://t.me/c/123/9"},
        )


def test_duplicate_yaml_key_is_rejected(tmp_path: Path) -> None:
    content = KEYWORD_PLAN.replace("window_days: 3", "window_days: 3\nwindow_days: 4")

    with pytest.raises(PlanLoadError, match="重复 YAML key: window_days"):
        load_china_plan(_write_plan(tmp_path, content), environ={})


@pytest.mark.parametrize(
    "target",
    [
        "prefix-${TELEGRAM_TARGET}",
        "{TELEGRAM_TARGET}",
        "${TELEGRAM_TARGET:-fallback}",
    ],
)
def test_only_whole_scalar_environment_reference_is_allowed(
    tmp_path: Path, target: str
) -> None:
    content = SELECTED_PLAN.replace(
        "target_url: ${TELEGRAM_TARGET}", f'target_url: "{target}"'
    )

    with pytest.raises(PlanLoadError, match="必须是完整标量"):
        load_china_plan(
            _write_plan(tmp_path, content),
            environ={"TELEGRAM_TARGET": "https://t.me/c/123/9"},
        )


def test_environment_value_is_not_recursively_expanded(tmp_path: Path) -> None:
    plan = load_china_plan(
        _write_plan(tmp_path, SELECTED_PLAN),
        environ={
            "TELEGRAM_TARGET": "${SECOND_TARGET}",
            "SECOND_TARGET": "https://t.me/c/123/9",
        },
    )

    assert (
        plan.market_scopes["a_share"].delivery.telegram.target_url == "${SECOND_TARGET}"
    )


def test_missing_environment_error_has_path_but_not_other_secret(
    tmp_path: Path,
) -> None:
    with pytest.raises(PlanLoadError) as exc_info:
        load_china_plan(
            _write_plan(tmp_path, SELECTED_PLAN),
            environ={"UNRELATED_SECRET": "do-not-leak"},
        )

    message = str(exc_info.value)
    assert "$.market_scopes.a_share.delivery.telegram.target_url" in message
    assert "TELEGRAM_TARGET" in message
    assert "do-not-leak" not in message


def test_plan_path_must_be_regular_file(tmp_path: Path) -> None:
    with pytest.raises(PlanLoadError, match="普通文件"):
        load_china_plan(tmp_path, environ={})


def test_app_settings_mask_secrets_and_require_single_bot_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+psycopg://file-secret/test\n"
        "TELEGRAM_BOT_TOKEN=file-bot-token\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://process-secret/test")

    settings = load_app_settings(env_file=env_file)

    assert settings.require_database_url().endswith("process-secret/test")
    assert settings.require_telegram_bot_token() == "file-bot-token"
    assert "process-secret" not in repr(settings)
    assert "file-bot-token" not in repr(settings)
    assert "**********" in repr(settings)


def test_app_settings_uses_default_env_file_but_process_environment_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql+psycopg://file-value/test\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://process-value/test")

    settings = load_app_settings()

    assert settings.require_database_url().endswith("process-value/test")


def test_app_settings_reports_missing_secret_without_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    settings = load_app_settings(env_file=tmp_path / "missing.env")

    with pytest.raises(ValueError, match="`TELEGRAM_BOT_TOKEN`"):
        settings.require_telegram_bot_token()
