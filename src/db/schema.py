from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg

SCHEMA_DIR = Path(__file__).resolve().parent / "sql"

RESET_SCHEMA_SQL = """
DROP TABLE IF EXISTS
    telegram_deliveries,
    announcement_summaries,
    announcement_hits,
    announcements
CASCADE
"""


def ensure_schema(conn: psycopg.Connection[Any]) -> None:
    """按文件名顺序执行 schema SQL，保证本地库结构存在。"""
    for schema_path in sorted(SCHEMA_DIR.glob("*.sql")):
        conn.execute(schema_path.read_text(encoding="utf-8"))


def reset_schema(conn: psycopg.Connection[Any]) -> None:
    """重建工作流表；仅由 CLI 的显式 reset 命令调用。"""
    conn.execute(RESET_SCHEMA_SQL)
    ensure_schema(conn)
