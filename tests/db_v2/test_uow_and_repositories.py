"""UnitOfWork、Provider raw 与 China aggregate 事务测试。"""

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from niuniu_stock_announcer.db.connection import create_session_factory
from niuniu_stock_announcer.db.errors import PersistenceConflictError
from niuniu_stock_announcer.db.model import (
    ChinaAnnouncementModel,
    CninfoAnnouncementModel,
    SseAnnouncementModel,
    SzseAnnouncementModel,
)
from niuniu_stock_announcer.db.repositories.china.announcements import (
    ChinaAnnouncementRepository,
)
from niuniu_stock_announcer.db.schema import (
    ChinaAnnouncementRecord,
    CninfoAnnouncementRecord,
    SseAnnouncementRecord,
    SzseAnnouncementRecord,
)
from niuniu_stock_announcer.db.unit_of_work import UnitOfWork
from tests.db_v2.factories import announcement, cninfo_raw, sse_raw, szse_raw

pytestmark = pytest.mark.postgres


def test_provider_raw_and_china_aggregate_commit_in_one_uow(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    with UnitOfWork(session_factory) as uow:
        cninfo_announcement = uow.china_announcements.upsert(announcement("cninfo-1"))
        cninfo_record = uow.cninfo_announcements.upsert(
            cninfo_raw(cninfo_announcement.id, "cninfo-1")
        )
        sse_announcement = uow.china_announcements.upsert(
            announcement("sse-1", provider_key="sse")
        )
        sse_record = uow.sse_announcements.upsert(sse_raw(sse_announcement.id, "sse-1"))
        szse_announcement = uow.china_announcements.upsert(
            announcement("szse-1", provider_key="szse")
        )
        szse_record = uow.szse_announcements.upsert(
            szse_raw(szse_announcement.id, "szse-1")
        )

    assert isinstance(cninfo_announcement, ChinaAnnouncementRecord)
    assert isinstance(cninfo_record, CninfoAnnouncementRecord)
    assert isinstance(sse_record, SseAnnouncementRecord)
    assert isinstance(szse_record, SzseAnnouncementRecord)
    assert cninfo_announcement.title == "测试公告 cninfo-1"
    assert cninfo_record.page_column == "SHKCB"
    assert sse_record.provider_announcement_id == "sse-1"
    assert szse_record.sec_codes == ("000510",)

    with Session(postgres_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(ChinaAnnouncementModel))
            == 3
        )
        assert (
            session.scalar(select(func.count()).select_from(CninfoAnnouncementModel))
            == 1
        )
        assert (
            session.scalar(select(func.count()).select_from(SseAnnouncementModel)) == 1
        )
        assert (
            session.scalar(select(func.count()).select_from(SzseAnnouncementModel)) == 1
        )


def test_exception_rolls_back_provider_and_aggregate(postgres_engine: Engine) -> None:
    session_factory = create_session_factory(postgres_engine)
    with pytest.raises(RuntimeError, match="force rollback"):
        with UnitOfWork(session_factory) as uow:
            record = uow.china_announcements.upsert(announcement("rollback-1"))
            uow.cninfo_announcements.upsert(cninfo_raw(record.id, "rollback-1"))
            raise RuntimeError("force rollback")

    with Session(postgres_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(ChinaAnnouncementModel))
            == 0
        )
        assert (
            session.scalar(select(func.count()).select_from(CninfoAnnouncementModel))
            == 0
        )


def test_savepoint_rolls_back_only_failed_provider_item(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    with UnitOfWork(session_factory) as uow:
        first = uow.china_announcements.upsert(announcement("raw-identity"))
        uow.cninfo_announcements.upsert(cninfo_raw(first.id, "raw-identity"))

        with pytest.raises(PersistenceConflictError):
            with uow.savepoint():
                conflicting = uow.china_announcements.upsert(
                    announcement("other-announcement")
                )
                uow.cninfo_announcements.upsert(
                    cninfo_raw(conflicting.id, "raw-identity")
                )

        final = uow.china_announcements.upsert(announcement("final-announcement"))
        uow.cninfo_announcements.upsert(cninfo_raw(final.id, "final-announcement"))

    with Session(postgres_engine) as session:
        identities = session.scalars(
            select(ChinaAnnouncementModel.provider_announcement_id).order_by(
                ChinaAnnouncementModel.provider_announcement_id
            )
        ).all()
        assert identities == ["final-announcement", "raw-identity"]


def test_repository_does_not_commit_callers_transaction(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    writer = session_factory()
    reader = session_factory()
    try:
        writer.begin()
        repository = ChinaAnnouncementRepository(writer)
        repository.upsert(announcement("uncommitted"))
        assert (
            reader.scalar(select(func.count()).select_from(ChinaAnnouncementModel)) == 0
        )

        writer.commit()
        reader.rollback()
        assert (
            reader.scalar(select(func.count()).select_from(ChinaAnnouncementModel)) == 1
        )
    finally:
        writer.close()
        reader.close()


class _CommitFailure(RuntimeError):
    pass


class _FailingSession:
    def __init__(self) -> None:
        self.rolled_back = False
        self.closed = False

    def begin(self) -> None:
        return None

    def commit(self) -> None:
        raise _CommitFailure("commit failed")

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_commit_failure_propagates_and_prevents_external_call() -> None:
    session = _FailingSession()
    external_calls: list[str] = []

    def factory() -> _FailingSession:
        return session

    with pytest.raises(_CommitFailure, match="commit failed"):
        with UnitOfWork(factory):
            pass
        external_calls.append("provider")

    assert external_calls == []
    assert session.rolled_back is True
    assert session.closed is True


def test_uow_instance_cannot_be_reused(postgres_engine: Engine) -> None:
    uow = UnitOfWork(create_session_factory(postgres_engine))
    with uow:
        pass
    with pytest.raises(RuntimeError, match="只能使用一次"):
        with uow:
            pass


@pytest.mark.parametrize(
    ("provider_key", "repository_name", "raw_factory"),
    [
        ("cninfo", "cninfo_announcements", cninfo_raw),
        ("sse", "sse_announcements", sse_raw),
        ("szse", "szse_announcements", szse_raw),
    ],
)
def test_provider_repository_reports_crossed_unique_identity_as_conflict(
    postgres_engine: Engine,
    provider_key: str,
    repository_name: str,
    raw_factory,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    with UnitOfWork(session_factory) as uow:
        first = uow.china_announcements.upsert(
            announcement("raw-first", provider_key=provider_key)
        )
        second = uow.china_announcements.upsert(
            announcement("raw-second", provider_key=provider_key)
        )
        repository = getattr(uow, repository_name)
        repository.upsert(raw_factory(first.id, "raw-first"))
        repository.upsert(raw_factory(second.id, "raw-second"))

    with pytest.raises(PersistenceConflictError, match="分别命中不同记录"):
        with UnitOfWork(session_factory) as uow:
            repository = getattr(uow, repository_name)
            repository.upsert(raw_factory(second.id, "raw-first"))
