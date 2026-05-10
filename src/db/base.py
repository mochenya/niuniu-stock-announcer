from __future__ import annotations

from typing import Any

import psycopg


class RepositoryBase:
    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn
