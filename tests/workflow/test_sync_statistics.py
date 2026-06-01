from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from cninfo_announcement.models import BusinessAnnouncement

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import workflow.sync as sync_module  # noqa: E402
from domain.search_models import HitUpsertResult, SearchTask  # noqa: E402


class _CommitFailingConnection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self) -> _CommitFailingConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1
        raise RuntimeError("commit failed")

    def rollback(self) -> None:
        self.rollbacks += 1


class _CommitSuccessfulConnection(_CommitFailingConnection):
    def commit(self) -> None:
        self.commits += 1


class _FakeRepository:
    def __init__(self, _conn: object) -> None:
        return None

    def upsert_announcement(self, _announcement: BusinessAnnouncement) -> bool:
        return True

    def upsert_hit(
        self,
        *,
        task: SearchTask,
        announcement: BusinessAnnouncement,
        decision: object,
    ) -> HitUpsertResult:
        return HitUpsertResult(
            hit_id=1,
            inserted=True,
            filter_status="selected",
        )

    def ensure_workflow_rows(
        self,
        *,
        hit_id: int,
        task: SearchTask,
        announcement: BusinessAnnouncement,
    ) -> bool:
        return True


class _FakeSearchResult:
    def __init__(self, announcement: BusinessAnnouncement) -> None:
        self.items = [announcement]
        self.response = SimpleNamespace(announcements=[announcement])


@contextmanager
def _fake_announcement_client(_source: str):
    yield object()


def _build_task() -> SearchTask:
    return SearchTask(
        announcement_source="cninfo",
        source_key="cninfo::sh::stock::600000::-",
        market="sh",
        stock_code="600000",
        stock_key="sh:600000",
        search_mode="stock",
    )


def _build_announcement() -> BusinessAnnouncement:
    return BusinessAnnouncement(
        source="cninfo",
        sec_code="600000",
        sec_name="测试公司",
        announcement_id="ann-1",
        announcement_title="测试公告",
        announcement_time=1,
    )


def _patch_sync_dependencies(
    monkeypatch,
    *,
    conn: object,
    task: SearchTask,
    announcement: BusinessAnnouncement,
) -> None:
    monkeypatch.setattr(
        sync_module,
        "load_runtime_config",
        lambda **_kwargs: SimpleNamespace(
            database_url="postgresql://example",
            watchlist_file=Path("unused.yaml"),
            window_days=3,
            sync_source_delay_seconds=0,
        ),
    )
    monkeypatch.setattr(
        sync_module,
        "load_watchlist_config",
        lambda _path: SimpleNamespace(window_days=None),
    )
    monkeypatch.setattr(sync_module, "build_search_tasks", lambda _config: [task])
    monkeypatch.setattr(sync_module, "connect_database", lambda _url: conn)
    monkeypatch.setattr(sync_module, "ensure_schema", lambda _conn: None)
    monkeypatch.setattr(sync_module, "AnnouncementRepository", _FakeRepository)
    monkeypatch.setattr(
        sync_module,
        "create_announcement_client",
        _fake_announcement_client,
    )
    monkeypatch.setattr(
        sync_module,
        "query_search_task",
        lambda *_args, **_kwargs: _FakeSearchResult(announcement),
    )


def test_sync_summary_merges_query_stats_after_successful_commit(monkeypatch) -> None:
    conn = _CommitSuccessfulConnection()
    task = _build_task()
    announcement = _build_announcement()
    _patch_sync_dependencies(
        monkeypatch,
        conn=conn,
        task=task,
        announcement=announcement,
    )

    summary = sync_module.sync_once(progress=lambda _event: None)

    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert summary.fetched_count == 1
    assert summary.inserted_announcements == 1
    assert summary.inserted_hits == 1
    assert summary.seeded_summaries == 1
    assert [ref.key for ref in summary.new_refs] == ["cninfo:ann-1"]
    assert summary.errors == []


def test_sync_summary_does_not_include_rolled_back_query(monkeypatch) -> None:
    conn = _CommitFailingConnection()
    task = _build_task()
    announcement = _build_announcement()
    _patch_sync_dependencies(
        monkeypatch,
        conn=conn,
        task=task,
        announcement=announcement,
    )

    summary = sync_module.sync_once(progress=lambda _event: None)

    assert conn.commits == 1
    assert conn.rollbacks == 1
    assert summary.fetched_count == 0
    assert summary.inserted_announcements == 0
    assert summary.inserted_hits == 0
    assert summary.seeded_summaries == 0
    assert summary.new_refs == []
    assert len(summary.errors) == 1
    assert "commit failed" in summary.errors[0]
