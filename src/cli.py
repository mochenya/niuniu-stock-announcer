from __future__ import annotations

import typer

from cli_config import config_app
from cli_retry_failed import retry_failed_app
from cli_workflow import register_workflow_commands

app = typer.Typer(
    help="NiuNiu Stock Announcer CLI",
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(retry_failed_app, name="retry-failed")
app.add_typer(config_app, name="config")
register_workflow_commands(app)


if __name__ == "__main__":
    app()
