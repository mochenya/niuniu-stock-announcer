"""重构前 Typer 命令面的特征快照。"""

from __future__ import annotations

from typer.main import get_command

from cli import app


def test_v1_root_command_surface() -> None:
    root = get_command(app)

    assert set(root.commands) == {
        "init-db",
        "sync",
        "run",
        "process-pending",
        "retry-failed",
        "config",
    }


def test_v1_workflow_option_surface() -> None:
    root = get_command(app)
    expected_options = {
        "sync": {
            "env_file",
            "config_file",
            "window_days",
            "log_level",
            "log_dir",
            "no_log_file",
        },
        "run": {
            "env_file",
            "config_file",
            "window_days",
            "limit",
            "log_level",
            "log_dir",
            "no_log_file",
        },
        "process-pending": {
            "env_file",
            "config_file",
            "limit",
            "log_level",
            "log_dir",
            "no_log_file",
        },
    }

    for command_name, option_names in expected_options.items():
        command = root.commands[command_name]
        assert {parameter.name for parameter in command.params} == option_names
