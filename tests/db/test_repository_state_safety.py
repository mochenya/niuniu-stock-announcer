from __future__ import annotations

import sys
from pathlib import Path

import pytest
from cninfo_announcement.models import BusinessAnnouncement

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from db.repository import AnnouncementRepository  # noqa: E402
from domain.workflow_models import WorkflowCandidate  # noqa: E402
from workflow.pending import _bump_and_skip_exhausted  # noqa: E402


class _FakeResult:
    def __init__(self, *, rowcount: int = 0, row: tuple[object, ...] | None = None):
        self.rowcount = rowcount
        self._row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class _FakeCursor:
    def __init__(self, conn: _FakeConnection):
        self._conn = conn

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self._conn.executed.append((query, params))

    def fetchall(self) -> list[dict[str, object]]:
        return []


class _FakeConnection:
    def __init__(self, *, rowcount: int = 0):
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.rowcount = rowcount

    def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> _FakeResult:
        self.executed.append((query, params))
        return _FakeResult(rowcount=self.rowcount)

    def cursor(self, *, row_factory: object) -> _FakeCursor:
        return _FakeCursor(self)


def _compact_sql(sql: str) -> str:
    return " ".join(sql.split())


def test_claim_summary_candidates_uses_locked_update_returning() -> None:
    conn = _FakeConnection()
    repo = AnnouncementRepository(conn)

    repo.claim_summary_candidates(statuses=("pending",), limit=2)

    sql = _compact_sql(conn.executed[0][0])
    assert "FOR UPDATE OF s SKIP LOCKED" in sql
    assert "UPDATE announcement_summaries" in sql
    assert "SET status = 'running'" in sql
    assert "RETURNING" in sql


def test_claim_delivery_candidates_uses_locked_update_returning() -> None:
    conn = _FakeConnection()
    repo = AnnouncementRepository(conn)

    repo.claim_delivery_candidates(statuses=("pending",), limit=2)

    sql = _compact_sql(conn.executed[0][0])
    assert "FOR UPDATE OF d SKIP LOCKED" in sql
    assert "UPDATE telegram_deliveries" in sql
    assert "SET status = 'running'" in sql
    assert "RETURNING" in sql


def test_reset_stale_running_summaries_uses_started_at_cutoff() -> None:
    conn = _FakeConnection(rowcount=3)
    repo = AnnouncementRepository(conn)

    count = repo.reset_stale_running_summaries(timeout_minutes=45)

    sql = _compact_sql(conn.executed[0][0])
    assert count == 3
    assert "status = 'running'" in sql
    assert "summary_started_at < now() - (%s * interval '1 minute')" in sql
    assert "SET status = 'failed'" in sql


def test_reset_stale_running_deliveries_marks_unknown_to_avoid_auto_retry() -> None:
    conn = _FakeConnection(rowcount=2)
    repo = AnnouncementRepository(conn)

    count = repo.reset_stale_running_deliveries(timeout_minutes=30)

    sql = _compact_sql(conn.executed[0][0])
    assert count == 2
    assert "status = 'running'" in sql
    assert "started_at < now() - (%s * interval '1 minute')" in sql
    assert "SET status = 'unknown'" in sql


def test_mark_summary_skipped_requires_downloaded_pdf() -> None:
    conn = _FakeConnection(rowcount=0)
    repo = AnnouncementRepository(conn)

    with pytest.raises(LookupError):
        repo.mark_summary_skipped(source="cninfo", announcement_id="ann-1")

    sql = _compact_sql(conn.executed[0][0])
    assert "status = 'failed'" in sql
    assert "pdf_local_path IS NOT NULL" in sql


def test_exhausted_summary_without_pdf_stays_failed_instead_of_skipped() -> None:
    conn = _FakeConnection()
    repo = AnnouncementRepository(conn)
    candidate = WorkflowCandidate(
        source="cninfo",
        announcement_id="ann-1",
        announcement=BusinessAnnouncement(
            source="cninfo",
            announcement_id="ann-1",
            announcement_title="test announcement",
            announcement_time=1,
        ),
        market="sh",
        stock_code="600000",
        stock_key="sh:600000",
        company_name="test company",
        summary_status="failed",
        summary_failure_count=3,
        pdf_local_path=None,
    )
    events = []

    remaining, skipped_refs = _bump_and_skip_exhausted(
        repo,
        conn=conn,
        candidates=[candidate],
        max_failures=3,
        progress=events.append,
    )

    assert remaining == []
    assert skipped_refs == []
    assert conn.executed == []
    assert events[0].event == "skip_blocked"
