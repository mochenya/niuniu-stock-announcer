from __future__ import annotations

from typing import Any

import psycopg


def connect_database(database_url: str) -> psycopg.Connection[Any]:
    return psycopg.connect(database_url)
