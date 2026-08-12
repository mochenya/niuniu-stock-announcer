"""只接受 application API 注入连接的 Alembic 环境。"""

from alembic import context
from sqlalchemy import Connection

from niuniu_stock_announcer.db.model import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    """在调用方事务中运行 migration，避免 Alembic 自行读取秘密 URL。"""
    connection = context.config.attributes.get("connection")
    if not isinstance(connection, Connection):
        raise RuntimeError("Alembic migration 必须由 application API 注入 Connection")
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("v2 migration 不支持脱离数据库的离线 SQL 模式")
run_migrations_online()
