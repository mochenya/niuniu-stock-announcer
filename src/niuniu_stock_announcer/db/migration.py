"""Alembic 的应用级 upgrade/current API。"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, Engine

MIGRATIONS_DIRECTORY = Path(__file__).with_name("migrations")


def upgrade_database(engine: Engine, revision: str = "head") -> None:
    """在调用方显式提供的 v2 Engine 上执行 Alembic upgrade。

    Args:
        engine: 已由 composition root 创建的 v2 PostgreSQL Engine。
        revision: Alembic 目标 revision，业务命令通常使用 `head`。
    """
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), revision)


def downgrade_database(engine: Engine, revision: str = "base") -> None:
    """为 migration 测试执行显式 downgrade；产品 CLI 不暴露该能力。

    Args:
        engine: 测试专用 PostgreSQL Engine。
        revision: Alembic 目标 revision，默认回到空库。
    """
    with engine.begin() as connection:
        command.downgrade(_alembic_config(connection), revision)


def get_current_revision(engine: Engine) -> str | None:
    """读取当前 database revision，不隐式升级或创建业务表。

    Args:
        engine: v2 PostgreSQL Engine。

    Returns:
        当前 revision；从未升级的空库返回 `None`。
    """
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _alembic_config(connection: Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIRECTORY))
    config.attributes["connection"] = connection
    return config
