from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row

from domain.workflow_models import AnnouncementRef


def fetchall(
    conn: psycopg.Connection[Any],
    query: str,
    params: Sequence[Any],
) -> list[dict[str, Any]]:
    """用 dict row 返回查询结果，保持 row_mappers 按列名取值。"""
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(query, tuple(params))
        return list(cursor.fetchall())


def append_limit(
    query: str,
    params: list[Any],
    limit: int | None,
) -> tuple[str, list[Any]]:
    """为候选查询追加 LIMIT；负数按 0 处理，避免意外全量扫描。"""
    if limit is None:
        return query, params
    params.append(max(limit, 0))
    return f"{query} LIMIT %s", params


def build_ref_clause(
    alias: str,
    refs: Sequence[AnnouncementRef],
) -> tuple[str, list[str]]:
    """为公告源和公告 ID 组合生成参数化过滤条件。

    空 refs 明确返回 FALSE，避免调用方传空列表时误查全表。
    """
    if not refs:
        return "FALSE", []
    clauses: list[str] = []
    params: list[str] = []
    for ref in refs:
        clauses.append(
            f"({alias}.announcement_source = %s AND {alias}.announcement_id = %s)"
        )
        params.extend([ref.source, ref.announcement_id])
    return f"({' OR '.join(clauses)})", params
