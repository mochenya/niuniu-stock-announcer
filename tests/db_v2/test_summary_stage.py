"""SummaryStage 的短事务、并发、retry 与原子物化集成测试。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from niuniu_stock_announcer.announcements.schema import (
    ChinaAnnouncement,
    StoredAnnouncementDocument,
)
from niuniu_stock_announcer.db.connection import create_session_factory
from niuniu_stock_announcer.db.model import (
    ChinaAnnouncementModel,
    ChinaSummaryModel,
    TelegramDocumentMessageModel,
    TelegramSummaryMessageModel,
)
from niuniu_stock_announcer.db.schema import (
    TelegramDocumentMessageWrite,
    TelegramSummaryMessageWrite,
)
from niuniu_stock_announcer.db.unit_of_work import UnitOfWork
from niuniu_stock_announcer.pipelines.china.stages.summary import SummaryStage
from niuniu_stock_announcer.summary.errors import SummaryError
from tests.db_v2.factories import (
    announcement,
    delivery,
    selected_match,
    summary_completion,
)

pytestmark = pytest.mark.postgres


class _FakeSummaryService:
    def __init__(
        self,
        tmp_path: Path,
        *,
        failures: list[Exception] | None = None,
        ensure_entered: Event | None = None,
        agent_entered: Event | None = None,
        release: Event | None = None,
        on_ensure=None,
        on_agent=None,
    ) -> None:
        self._tmp_path = tmp_path
        self._failures = list(failures or [])
        self._ensure_entered = ensure_entered
        self._agent_entered = agent_entered
        self._release = release
        self._on_ensure = on_ensure
        self._on_agent = on_agent
        self._lock = Lock()
        self.ensure_calls = 0
        self.agent_calls = 0

    def ensure_pdf(self, value: ChinaAnnouncement) -> StoredAnnouncementDocument:
        with self._lock:
            self.ensure_calls += 1
        if self._on_ensure is not None:
            self._on_ensure(value)
        if self._ensure_entered is not None:
            self._ensure_entered.set()
        if self._ensure_entered is not None and self._release is not None:
            assert self._release.wait(timeout=5)
        path = self._tmp_path / f"{value.provider_announcement_id}.pdf"
        return StoredAnnouncementDocument(
            provider_key=value.provider_key,
            provider_announcement_id=value.provider_announcement_id,
            source_url=value.source_url,
            storage_relative_path=(
                f"{value.provider_key}/2026/08/{value.provider_announcement_id}.pdf"
            ),
            local_path=path,
            size_bytes=4096,
            sha256="a" * 64,
            page_count=1,
        )

    def summarize_document(self, value, document):
        with self._lock:
            self.agent_calls += 1
            failure = self._failures.pop(0) if self._failures else None
        if self._on_agent is not None:
            self._on_agent(value, document)
        if self._agent_entered is not None:
            self._agent_entered.set()
        if self._agent_entered is not None and self._release is not None:
            assert self._release.wait(timeout=5)
        if failure is not None:
            raise failure
        return summary_completion()


class _CommitFailingContext:
    def __init__(self, session_factory, *, fail: bool) -> None:
        self._inner = UnitOfWork(session_factory)
        self._fail = fail

    def __enter__(self) -> UnitOfWork:
        return self._inner.__enter__()

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if exc_type is None and self._fail:
            error = RuntimeError("simulated commit failure")
            self._inner.__exit__(RuntimeError, error, None)
            raise error
        return self._inner.__exit__(exc_type, exc_value, traceback)


class _CommitFailingUowFactory:
    def __init__(self, session_factory, *, fail_calls: set[int]) -> None:
        self._session_factory = session_factory
        self._fail_calls = fail_calls
        self.calls = 0

    def __call__(self) -> _CommitFailingContext:
        self.calls += 1
        return _CommitFailingContext(
            self._session_factory, fail=self.calls in self._fail_calls
        )


def _create_summary(
    session_factory,
    *,
    identity: str,
    plan_key: str = "plan-alpha",
    send_original_document: bool = True,
) -> tuple[int, int, int]:
    with UnitOfWork(session_factory) as uow:
        record = uow.china_announcements.upsert(announcement(identity))
        uow.china_matches.record(selected_match(record.id, plan_key=plan_key))
        summary = uow.china_summaries.ensure(record.id)
        uow.china_summaries.lock(summary.id)
        parent = uow.telegram.ensure_delivery(
            delivery(
                summary.id,
                plan_key=plan_key,
                send_original_document=send_original_document,
            )
        )
        return record.id, summary.id, parent.id


def _materialize(uow: UnitOfWork, summary_id: int, delivery_id: int) -> None:
    context = uow.china_summaries.get_render_context(summary_id)
    deliveries = uow.telegram.list_deliveries(
        producer_key="china_summary", business_key=str(summary_id)
    )
    parent = next(item for item in deliveries if item.id == delivery_id)
    if context.summary.status == "completed":
        assert context.summary.result is not None
        text = f"{parent.plan_key}:{context.summary.result.summary_text}"
    else:
        assert context.summary.status == "skipped"
        text = f"{parent.plan_key}:摘要降级:{context.summary.failure_reason}"
    uow.telegram.insert_summary_message(
        TelegramSummaryMessageWrite(
            telegram_delivery_id=delivery_id,
            text_content=text,
        )
    )
    if parent.send_original_document or context.summary.status == "skipped":
        pdf = context.announcement.pdf
        assert pdf is not None
        uow.telegram.insert_document_message(
            TelegramDocumentMessageWrite(
                telegram_delivery_id=delivery_id,
                document_key="original",
                source_url=context.announcement.source_url,
                storage_relative_path=pdf.storage_relative_path,
                document_filename="announcement.pdf",
                document_mime_type="application/pdf",
                document_size_bytes=pdf.size_bytes,
                document_sha256=pdf.sha256,
                document_caption=context.announcement.title,
            )
        )


def _stage(service, session_factory, *, max_failures: int = 3) -> SummaryStage:
    return SummaryStage(
        service,
        lambda: UnitOfWork(session_factory),
        _materialize,
        max_failures=max_failures,
    )


def test_summary_stage_commits_claim_and_pdf_before_external_agent(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    announcement_id, summary_id, delivery_id = _create_summary(
        session_factory, identity="stage-success"
    )

    def assert_claim_committed(_announcement: ChinaAnnouncement) -> None:
        with Session(postgres_engine) as session:
            assert (
                session.scalar(
                    select(ChinaSummaryModel.status).where(
                        ChinaSummaryModel.id == summary_id
                    )
                )
                == "running"
            )

    def assert_pdf_committed(_announcement, _document) -> None:
        with Session(postgres_engine) as session:
            assert (
                session.scalar(
                    select(ChinaAnnouncementModel.pdf_sha256).where(
                        ChinaAnnouncementModel.id == announcement_id
                    )
                )
                == "a" * 64
            )

    service = _FakeSummaryService(
        tmp_path,
        on_ensure=assert_claim_committed,
        on_agent=assert_pdf_committed,
    )
    stage = _stage(service, session_factory)

    result = stage.execute(summary_ids=[summary_id])

    assert result.model_dump(exclude={"errors"}) == {
        "claimed_count": 1,
        "completed_count": 1,
        "failed_count": 0,
        "skipped_count": 0,
    }
    assert result.errors == ()
    assert service.ensure_calls == service.agent_calls == 1
    with Session(postgres_engine) as session:
        summary = session.get(ChinaSummaryModel, summary_id)
        assert summary is not None and summary.status == "completed"
        assert summary.summary_text == "回购事项摘要"
        assert summary.summary_tags == ["股份回购", "回购进展", "A股"]
        assert summary.summary_result == {
            "schema_version": "china-announcement-summary.v1",
            "summary_text": "回购事项摘要",
            "summary_tags": ["股份回购", "回购进展", "A股"],
        }
        assert (
            session.scalar(
                select(TelegramSummaryMessageModel.telegram_delivery_id).where(
                    TelegramSummaryMessageModel.telegram_delivery_id == delivery_id
                )
            )
            == delivery_id
        )

    repeated = stage.execute(summary_ids=[summary_id])
    assert repeated.claimed_count == 0
    assert service.ensure_calls == service.agent_calls == 1


def test_summary_stage_rejects_invalid_mode_before_opening_uow(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    service = _FakeSummaryService(tmp_path)
    stage = _stage(service, session_factory)

    with pytest.raises(ValueError, match="mode 只能"):
        stage.execute(mode="unknown")  # type: ignore[arg-type]

    assert service.ensure_calls == service.agent_calls == 0


def test_concurrent_summary_stages_claim_once_and_call_agent_once(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    _, summary_id, _ = _create_summary(session_factory, identity="concurrent-summary")
    ensure_entered = Event()
    release = Event()
    service = _FakeSummaryService(
        tmp_path, ensure_entered=ensure_entered, release=release
    )
    first_stage = _stage(service, session_factory)
    second_stage = _stage(service, session_factory)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(first_stage.execute, summary_ids=[summary_id])
        assert ensure_entered.wait(timeout=5)
        second = executor.submit(second_stage.execute, summary_ids=[summary_id])
        second_result = second.result(timeout=5)
        release.set()
        first_result = first.result(timeout=5)

    assert sorted([first_result.claimed_count, second_result.claimed_count]) == [0, 1]
    assert service.ensure_calls == service.agent_calls == 1


def test_plan_created_before_or_after_terminal_reuses_one_agent_result(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    announcement_id, summary_id, _ = _create_summary(
        session_factory, identity="plan-race"
    )
    agent_entered = Event()
    release = Event()
    service = _FakeSummaryService(
        tmp_path, agent_entered=agent_entered, release=release
    )
    stage = _stage(service, session_factory)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(stage.execute, summary_ids=[summary_id])
        assert agent_entered.wait(timeout=5)
        with UnitOfWork(session_factory) as uow:
            uow.china_matches.record(
                selected_match(announcement_id, plan_key="plan-beta")
            )
            locked = uow.china_summaries.lock(summary_id)
            assert locked.status == "running"
            beta = uow.telegram.ensure_delivery(
                delivery(summary_id, plan_key="plan-beta")
            )
        release.set()
        assert future.result(timeout=5).completed_count == 1

    with UnitOfWork(session_factory) as uow:
        uow.china_matches.record(selected_match(announcement_id, plan_key="plan-gamma"))
        locked = uow.china_summaries.lock(summary_id)
        assert locked.status == "completed"
        gamma = uow.telegram.ensure_delivery(
            delivery(summary_id, plan_key="plan-gamma")
        )
        _materialize(uow, summary_id, gamma.id)

    assert service.agent_calls == 1
    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramSummaryMessageModel)
            )
            == 3
        )
        assert (
            session.scalar(
                select(TelegramSummaryMessageModel.id).where(
                    TelegramSummaryMessageModel.telegram_delivery_id == beta.id
                )
            )
            is not None
        )
        frozen = session.scalar(
            select(TelegramSummaryMessageModel.text_content).where(
                TelegramSummaryMessageModel.telegram_delivery_id == gamma.id
            )
        )

    with UnitOfWork(session_factory) as uow:
        uow.china_summaries.lock(summary_id)
        _materialize(uow, summary_id, gamma.id)
    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(TelegramSummaryMessageModel.text_content).where(
                    TelegramSummaryMessageModel.telegram_delivery_id == gamma.id
                )
            )
            == frozen
        )


def test_retry_failure_count_reaches_skipped_only_with_persisted_pdf(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    _, summary_id, delivery_id = _create_summary(
        session_factory,
        identity="retry-skipped",
        send_original_document=False,
    )
    service = _FakeSummaryService(
        tmp_path,
        failures=[
            SummaryError("REQUEST", "provider unavailable"),
            SummaryError("REPAIR_EXHAUSTED", "invalid schema"),
        ],
    )
    stage = _stage(service, session_factory, max_failures=2)

    first = stage.execute(summary_ids=[summary_id])
    assert first.failed_count == 1
    with Session(postgres_engine) as session:
        summary = session.get(ChinaSummaryModel, summary_id)
        assert summary is not None and summary.status == "failed"
        assert summary.failure_count == 1
        assert (
            session.scalar(
                select(ChinaAnnouncementModel.pdf_sha256).where(
                    ChinaAnnouncementModel.id == summary.china_announcement_id
                )
            )
            == "a" * 64
        )

    retried = stage.execute(mode="failed", summary_ids=[summary_id])

    assert retried.claimed_count == 1
    assert retried.skipped_count == 1
    assert retried.failed_count == 0
    assert service.agent_calls == 2
    with Session(postgres_engine) as session:
        summary = session.get(ChinaSummaryModel, summary_id)
        assert summary is not None and summary.status == "skipped"
        assert summary.failure_count == 2
        assert summary.summary_result is None
        assert (
            session.scalar(
                select(TelegramSummaryMessageModel.id).where(
                    TelegramSummaryMessageModel.telegram_delivery_id == delivery_id
                )
            )
            is not None
        )
        # skipped 强制物化原公告，即使 delivery 正常意图关闭 document。
        assert (
            session.scalar(
                select(TelegramDocumentMessageModel.id).where(
                    TelegramDocumentMessageModel.telegram_delivery_id == delivery_id
                )
            )
            is not None
        )


def test_exhausted_retry_without_pdf_stays_failed_and_never_calls_service(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    _, summary_id, _ = _create_summary(session_factory, identity="skip-without-pdf")
    for mode in ("pending", "failed"):
        with UnitOfWork(session_factory) as uow:
            claim = uow.china_summaries.claim_next(mode=mode)
            assert claim is not None and claim.summary.id == summary_id
            uow.china_summaries.save_failed(
                summary_id,
                reason="document unavailable",
                failure_log="DocumentValidationError",
            )
    service = _FakeSummaryService(tmp_path)
    stage = _stage(service, session_factory, max_failures=2)

    result = stage.execute(mode="failed", summary_ids=[summary_id])

    assert result.claimed_count == result.skipped_count == 0
    assert service.ensure_calls == service.agent_calls == 0
    with Session(postgres_engine) as session:
        summary = session.get(ChinaSummaryModel, summary_id)
        assert summary is not None and summary.status == "failed"
        assert summary.failure_count == 2


def test_claim_commit_failure_rolls_back_before_document_or_agent(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    _, summary_id, _ = _create_summary(session_factory, identity="claim-commit-failure")
    service = _FakeSummaryService(tmp_path)
    failing_factory = _CommitFailingUowFactory(session_factory, fail_calls={1})
    stage = SummaryStage(
        service,
        failing_factory,
        _materialize,
        max_failures=3,
    )

    result = stage.execute(summary_ids=[summary_id])

    assert result.claimed_count == 0
    assert result.errors[0].phase == "claim"
    assert service.ensure_calls == service.agent_calls == 0
    with Session(postgres_engine) as session:
        assert (
            session.scalar(
                select(ChinaSummaryModel.status).where(
                    ChinaSummaryModel.id == summary_id
                )
            )
            == "pending"
        )


def test_pdf_snapshot_commit_failure_stops_before_agent_and_records_failed(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    announcement_id, summary_id, _ = _create_summary(
        session_factory, identity="pdf-commit-failure"
    )
    service = _FakeSummaryService(tmp_path)
    # pending 执行的 UoW 顺序是 claim、PDF 快照、failure persistence。
    failing_factory = _CommitFailingUowFactory(session_factory, fail_calls={2})
    stage = SummaryStage(
        service,
        failing_factory,
        _materialize,
        max_failures=3,
    )

    result = stage.execute(summary_ids=[summary_id])

    assert result.failed_count == 1
    assert result.errors[0].phase == "pdf_persist"
    assert service.ensure_calls == 1
    assert service.agent_calls == 0
    with Session(postgres_engine) as session:
        summary = session.get(ChinaSummaryModel, summary_id)
        assert summary is not None and summary.status == "failed"
        assert summary.failure_count == 1
        assert (
            session.scalar(
                select(ChinaAnnouncementModel.pdf_sha256).where(
                    ChinaAnnouncementModel.id == announcement_id
                )
            )
            is None
        )


def test_materializer_failure_rolls_back_completed_and_partial_children(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    _, summary_id, delivery_id = _create_summary(
        session_factory, identity="materializer-failure"
    )
    service = _FakeSummaryService(tmp_path)

    def failing_materializer(
        uow: UnitOfWork, _summary_id: int, target_delivery_id: int
    ) -> None:
        uow.telegram.insert_summary_message(
            TelegramSummaryMessageWrite(
                telegram_delivery_id=target_delivery_id,
                text_content="必须随终态事务回滚的部分 payload",
            )
        )
        raise RuntimeError("materializer failed")

    stage = SummaryStage(
        service,
        lambda: UnitOfWork(session_factory),
        failing_materializer,
        max_failures=3,
    )

    result = stage.execute(summary_ids=[summary_id])

    assert result.failed_count == 1
    assert result.errors[0].phase == "terminal"
    assert service.ensure_calls == service.agent_calls == 1
    with Session(postgres_engine) as session:
        summary = session.get(ChinaSummaryModel, summary_id)
        assert summary is not None and summary.status == "failed"
        assert summary.summary_result is None
        assert (
            session.scalar(
                select(TelegramSummaryMessageModel.id).where(
                    TelegramSummaryMessageModel.telegram_delivery_id == delivery_id
                )
            )
            is None
        )


def test_failure_persistence_commit_failure_is_not_counted_as_failed(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    _, summary_id, _ = _create_summary(
        session_factory, identity="failure-persist-commit-failure"
    )
    service = _FakeSummaryService(
        tmp_path,
        failures=[SummaryError("REQUEST", "provider unavailable")],
    )
    # pending 执行的 UoW 顺序是 claim、PDF 快照、failure persistence。
    failing_factory = _CommitFailingUowFactory(session_factory, fail_calls={3})
    stage = SummaryStage(
        service,
        failing_factory,
        _materialize,
        max_failures=3,
    )

    result = stage.execute(summary_ids=[summary_id])

    assert result.claimed_count == 1
    assert result.failed_count == 0
    assert result.errors[0].phase == "failure_persist"
    assert service.ensure_calls == service.agent_calls == 1
    with Session(postgres_engine) as session:
        summary = session.get(ChinaSummaryModel, summary_id)
        assert summary is not None and summary.status == "running"
        assert summary.failure_count == 0


def test_terminal_commit_failure_rolls_back_payload_then_records_failed_once(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    _, summary_id, _ = _create_summary(
        session_factory, identity="terminal-commit-failure"
    )
    service = _FakeSummaryService(tmp_path)
    # pending 执行的 UoW 顺序是 claim、PDF 快照、terminal、failure persistence。
    failing_factory = _CommitFailingUowFactory(session_factory, fail_calls={3})
    stage = SummaryStage(
        service,
        failing_factory,
        _materialize,
        max_failures=3,
    )

    result = stage.execute(summary_ids=[summary_id])

    assert result.failed_count == 1
    assert result.errors[0].phase == "terminal"
    assert service.ensure_calls == service.agent_calls == 1
    with Session(postgres_engine) as session:
        summary = session.get(ChinaSummaryModel, summary_id)
        assert summary is not None and summary.status == "failed"
        assert summary.failure_count == 1
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramSummaryMessageModel)
            )
            == 0
        )
        assert (
            session.scalar(
                select(ChinaAnnouncementModel.pdf_sha256).where(
                    ChinaAnnouncementModel.id == summary.china_announcement_id
                )
            )
            == "a" * 64
        )


def test_summary_stage_recovers_stale_running_for_explicit_retry(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    _, summary_id, _ = _create_summary(session_factory, identity="stage-stale")
    with UnitOfWork(session_factory) as uow:
        claim = uow.china_summaries.claim_next()
        assert claim is not None and claim.summary.id == summary_id
    old_time = datetime.now(UTC) - timedelta(hours=3)
    with postgres_engine.begin() as connection:
        connection.execute(
            update(ChinaSummaryModel)
            .where(ChinaSummaryModel.id == summary_id)
            .values(started_at=old_time)
        )
    service = _FakeSummaryService(tmp_path)
    stage = _stage(service, session_factory)

    recovered = stage.recover_stale(
        started_before=datetime.now(UTC) - timedelta(hours=2)
    )

    assert recovered == 1
    with Session(postgres_engine) as session:
        summary = session.get(ChinaSummaryModel, summary_id)
        assert summary is not None and summary.status == "failed"
        assert summary.failure_count == 1
    assert service.ensure_calls == service.agent_calls == 0
