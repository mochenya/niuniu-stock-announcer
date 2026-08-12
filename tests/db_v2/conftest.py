"""v2 PostgreSQL 集成测试的自管临时数据库。"""

from __future__ import annotations

import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import Engine

from niuniu_stock_announcer.db.connection import create_db_engine
from niuniu_stock_announcer.db.migration import upgrade_database

POSTGRES_BIN = Path("/usr/lib/postgresql/16/bin")


def _free_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture(scope="session")
def postgres_admin_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """启动只供本测试会话使用的 PostgreSQL 16 cluster。

    Args:
        tmp_path_factory: pytest 会话级临时目录工厂。

    Yields:
        连接临时 cluster 内 `postgres` 管理库的 SQLAlchemy URL。
    """
    required = ("initdb", "pg_ctl", "postgres")
    missing = [name for name in required if not (POSTGRES_BIN / name).is_file()]
    if missing:
        pytest.fail("缺少 PostgreSQL 测试二进制: " + ", ".join(missing))

    cluster_root = tmp_path_factory.mktemp("postgres-v2")
    data_directory = cluster_root / "data"
    log_path = cluster_root / "postgres.log"
    socket_directory = cluster_root / "socket"
    socket_directory.mkdir()
    port = _free_loopback_port()
    subprocess.run(
        [
            str(POSTGRES_BIN / "initdb"),
            "-D",
            str(data_directory),
            "--auth=trust",
            "--username=postgres",
            "--no-locale",
            "--encoding=UTF8",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            str(POSTGRES_BIN / "pg_ctl"),
            "-D",
            str(data_directory),
            "-l",
            str(log_path),
            "-o",
            f"-h 127.0.0.1 -p {port} -k {socket_directory}",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    admin_url = f"postgresql+psycopg://postgres@127.0.0.1:{port}/postgres"
    try:
        yield admin_url
    finally:
        subprocess.run(
            [
                str(POSTGRES_BIN / "pg_ctl"),
                "-D",
                str(data_directory),
                "-m",
                "immediate",
                "-w",
                "stop",
            ],
            check=True,
            capture_output=True,
            text=True,
        )


@pytest.fixture
def empty_postgres_engine(postgres_admin_url: str) -> Iterator[Engine]:
    """为单个测试创建并销毁全新 PostgreSQL database。

    Args:
        postgres_admin_url: 测试会话自管 cluster 的管理库 URL。

    Yields:
        尚未运行 Alembic 的空库 Engine。
    """
    database_name = f"niuniu_v2_{uuid4().hex}"
    admin_dsn = postgres_admin_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
        )
    database_url = postgres_admin_url.rsplit("/", maxsplit=1)[0] + f"/{database_name}"
    engine = create_db_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            connection.execute(
                sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name))
            )


@pytest.fixture
def postgres_engine(empty_postgres_engine: Engine) -> Engine:
    """返回已由 Alembic 升级到 head 的测试 Engine。

    Args:
        empty_postgres_engine: 单测试专用空 PostgreSQL database。

    Returns:
        具有完整 v2 schema 的 Engine。
    """
    upgrade_database(empty_postgres_engine)
    return empty_postgres_engine
