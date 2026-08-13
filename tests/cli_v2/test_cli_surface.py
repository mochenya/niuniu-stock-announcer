"""v2 固定 CLI 命令面和 Plan 文件边界测试。"""

from __future__ import annotations

from pathlib import Path

from typer.main import get_command
from typer.testing import CliRunner

from niuniu_stock_announcer.cli import app


def test_root_command_surface_is_fixed() -> None:
    command = get_command(app)

    assert set(command.commands) == {
        "plan",
        "sync",
        "run",
        "process-pending",
        "retry-failed",
        "db",
    }
    assert set(command.commands["plan"].commands) == {"validate"}
    assert set(command.commands["retry-failed"].commands) == {
        "summary",
        "telegram",
        "all",
    }
    assert set(command.commands["db"].commands) == {"upgrade", "current"}


def test_plan_commands_require_exactly_one_regular_yaml_file(tmp_path: Path) -> None:
    runner = CliRunner()
    plan = tmp_path / "plan.yaml"
    plan.write_text("market: china\n", encoding="utf-8")

    for args in (
        ["sync"],
        ["run"],
        ["plan", "validate"],
        ["sync", "--plan", str(plan), "--plan", str(plan)],
        ["sync", "--plan", str(tmp_path)],
        ["sync", "--plan", str(tmp_path / "*.yaml")],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 2, (args, result.output)
        assert "错误" in result.output


def test_recovery_commands_do_not_expose_plan_option() -> None:
    command = get_command(app)
    for name in ("process-pending",):
        assert "plan" not in {
            parameter.name for parameter in command.commands[name].params
        }
    retry = command.commands["retry-failed"]
    for name in ("summary", "telegram", "all"):
        assert "plan" not in {
            parameter.name for parameter in retry.commands[name].params
        }
