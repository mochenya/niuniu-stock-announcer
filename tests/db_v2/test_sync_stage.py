"""China SyncStage 的真实 PostgreSQL 数据流与事务边界测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from niuniu_stock_announcer.announcements.schema import (
    AnnouncementQuery,
    AnnouncementSecurity,
    ChinaAnnouncement,
    CninfoSourceSnapshot,
    ProviderAnnouncement,
    ProviderQueryResult,
    SseSourceSnapshot,
    SzseSourceSnapshot,
)
from niuniu_stock_announcer.db.connection import create_session_factory
from niuniu_stock_announcer.db.model import (
    ChinaAnnouncementMatchModel,
    ChinaAnnouncementModel,
    ChinaSummaryModel,
    CninfoAnnouncementModel,
    SseAnnouncementModel,
    SzseAnnouncementModel,
    TelegramDeliveryModel,
    TelegramSummaryMessageModel,
)
from niuniu_stock_announcer.db.schema import TelegramSummaryMessageWrite
from niuniu_stock_announcer.db.unit_of_work import UnitOfWork
from niuniu_stock_announcer.im.telegram.schema import TelegramTarget
from niuniu_stock_announcer.pipelines.china.discovery.schema import DiscoveryQueryTask
from niuniu_stock_announcer.pipelines.china.provider_resolver import (
    ChinaProviderResolver,
)
from niuniu_stock_announcer.pipelines.china.schema import (
    AnnouncementProviderRoutes,
    TelegramTargetPlan,
)
from niuniu_stock_announcer.pipelines.china.stages.sync import SyncStage
from tests.db_v2.factories import summary_completion

pytestmark = pytest.mark.postgres


class _FakeProvider:
    def __init__(
        self,
        provider_key: str,
        responses: dict[tuple[str | None, str | None], ProviderQueryResult | Exception],
        *,
        uow_calls: list[str] | None = None,
    ) -> None:
        self.provider_key = provider_key
        self.responses = responses
        self.queries: list[AnnouncementQuery] = []
        self._uow_calls = uow_calls

    def query(self, query: AnnouncementQuery) -> ProviderQueryResult:
        if self._uow_calls is not None:
            assert self._uow_calls == []
        self.queries.append(query)
        response = self.responses[(query.stock_code, query.search_keyword)]
        if isinstance(response, Exception):
            raise response
        return response

    def download_pdf(self, _announcement, *, target_path):
        raise AssertionError("SyncStage 不应下载 PDF")

    def close(self) -> None:
        return None


def _task(
    *,
    plan_key: str,
    provider_key: str = "cninfo",
    exchange: str = "sh",
    scope: str = "a_share",
    stock_code: str | None = "688090",
    search_keyword: str | None = None,
    excluded: tuple[str, ...] = (),
    target: TelegramTargetPlan | None = None,
) -> DiscoveryQueryTask:
    return DiscoveryQueryTask(
        plan_key=plan_key,
        discovery_type=(
            "selected_stocks" if stock_code is not None else "market_keywords"
        ),
        market_scope=scope,
        provider_key=provider_key,
        query=AnnouncementQuery(
            exchange=exchange,
            market_scope=scope,
            start_date=date(2026, 8, 11),
            end_date=date(2026, 8, 12),
            stock_code=stock_code,
            search_keyword=search_keyword,
        ),
        title_exclude_keywords=excluded,
        target=target,
    )


def _provider_item(
    identity: str,
    *,
    provider_key: str = "cninfo",
    exchange: str = "sh",
    scope: str = "a_share",
    title: str = "关于股份回购与中标的公告",
) -> ProviderAnnouncement:
    source_url = {
        "cninfo": f"https://static.cninfo.com.cn/finalpage/{identity}.PDF",
        "sse": f"https://static.sse.com.cn/disclosure/{identity}.pdf",
        "szse": f"https://disc.static.szse.cn/download/disc/{identity}.PDF",
    }[provider_key]
    stock_code = "000510" if exchange == "sz" else "688090"
    announcement = ChinaAnnouncement(
        provider_key=provider_key,
        provider_announcement_id=identity,
        market_scope=scope,
        securities=(
            AnnouncementSecurity(
                exchange=exchange,
                stock_code=stock_code,
                stock_name="测试公司",
            ),
        ),
        title=title,
        published_at=datetime(2026, 8, 12, tzinfo=UTC),
        source_url=source_url,
    )
    if provider_key == "cninfo":
        snapshot = CninfoSourceSnapshot(
            announcement_id=identity,
            sec_code=stock_code,
            sec_name="测试公司",
            announcement_title=title,
            announcement_time_ms=1786492800000,
            adjunct_url=f"finalpage/{identity}.PDF",
            adjunct_type="PDF",
        )
    elif provider_key == "sse":
        snapshot = SseSourceSnapshot(
            provider_announcement_id=identity,
            security_code=stock_code,
            security_name="测试公司",
            title=title,
            sse_date="2026-08-12",
            url=f"/disclosure/{identity}.pdf",
        )
    else:
        snapshot = SzseSourceSnapshot(
            provider_announcement_id=identity,
            ann_id=identity,
            sec_codes=(stock_code,),
            sec_names=("测试公司",),
            title=title,
            publish_time="2026-08-12 00:00:00",
            attach_path=f"/disc/{identity}.PDF",
            attach_format="PDF",
        )
    return ProviderAnnouncement(
        announcement=announcement,
        source_snapshot=snapshot,
    )


def _query_result(
    provider_key: str, *items: ProviderAnnouncement
) -> ProviderQueryResult:
    return ProviderQueryResult(provider_key=provider_key, items=items)


def _target(url: str = "https://t.me/c/123456/9") -> TelegramTargetPlan:
    return TelegramTargetPlan(
        target_key="a-share-topic",
        target_url=url,
        send_original_document=True,
    )


def _target_parser(_url: str) -> TelegramTarget:
    return TelegramTarget(chat_id=-100123456, message_thread_id=9)


def _no_materialize(_uow: UnitOfWork, _summary_id: int, _delivery_id: int) -> None:
    return None


def _stage(
    *,
    routes: AnnouncementProviderRoutes,
    providers: dict[str, _FakeProvider],
    uow_factory,
    target_parser=_target_parser,
    materializer=_no_materialize,
) -> SyncStage:
    return SyncStage(
        ChinaProviderResolver(routes, providers),
        uow_factory,
        target_parser,
        materializer,
    )


def test_selected_multi_query_persists_once_freezes_first_evidence_and_activates(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    item = _provider_item("selected-1")
    uow_calls: list[str] = []
    provider = _FakeProvider(
        "cninfo",
        {
            ("688090", None): _query_result("cninfo", item),
            ("600000", None): _query_result("cninfo", item),
        },
        uow_calls=uow_calls,
    )

    def uow_factory() -> UnitOfWork:
        uow_calls.append("created")
        return UnitOfWork(session_factory)

    stage = _stage(
        routes=AnnouncementProviderRoutes(),
        providers={"cninfo": provider},
        uow_factory=uow_factory,
    )
    tasks = (
        _task(plan_key="selected-plan", stock_code="688090", target=_target()),
        _task(plan_key="selected-plan", stock_code="600000", target=_target()),
    )

    first = stage.execute(tasks)

    assert first.queries_succeeded == 2
    assert first.persisted_items == 1
    assert first.created_matches == 1
    assert first.selected_matches == 1
    assert len(first.activations) == 1
    assert first.activations[0].delivery_id is not None
    assert first.errors == ()
    with Session(postgres_engine) as session:
        match = session.scalar(select(ChinaAnnouncementMatchModel))
        delivery = session.scalar(select(TelegramDeliveryModel))
        assert match is not None and match.hit_count == 2
        assert match.query_stock_code == "688090"
        assert match.filter_status == "selected"
        assert delivery is not None
        assert delivery.target_url == "https://t.me/c/123456/9"
        assert delivery.send_original_document is True
        assert (
            session.scalar(select(func.count()).select_from(ChinaAnnouncementModel))
            == 1
        )
        assert (
            session.scalar(select(func.count()).select_from(CninfoAnnouncementModel))
            == 1
        )
        assert session.scalar(select(func.count()).select_from(ChinaSummaryModel)) == 1

    changed_tasks = tuple(
        task.model_copy(
            update={
                "title_exclude_keywords": ("回购",),
                "target": _target("https://t.me/c/999999/10"),
            }
        )
        for task in tasks
    )
    uow_calls.clear()
    repeated = stage.execute(changed_tasks)

    assert repeated.repeated_matches == 1
    assert repeated.activations == ()
    with Session(postgres_engine) as session:
        match = session.scalar(select(ChinaAnnouncementMatchModel))
        delivery = session.scalar(select(TelegramDeliveryModel))
        assert match is not None and match.hit_count == 4
        assert match.filter_status == "selected"
        assert match.filter_decisions[0]["reason_code"] == "passed"
        assert delivery is not None
        assert delivery.target_url == "https://t.me/c/123456/9"
        assert (
            session.scalar(select(func.count()).select_from(TelegramDeliveryModel)) == 1
        )


def test_keyword_queries_aggregate_evidence_without_duplicate_match(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    item = _provider_item("keyword-1")
    provider = _FakeProvider(
        "cninfo",
        {
            (None, "回购"): _query_result("cninfo", item),
            (None, "中标"): _query_result("cninfo", item),
        },
    )
    stage = _stage(
        routes=AnnouncementProviderRoutes(),
        providers={"cninfo": provider},
        uow_factory=lambda: UnitOfWork(session_factory),
    )

    result = stage.execute(
        (
            _task(
                plan_key="keyword-plan",
                stock_code=None,
                search_keyword="回购",
            ),
            _task(
                plan_key="keyword-plan",
                stock_code=None,
                search_keyword="中标",
            ),
        )
    )

    assert result.queries_succeeded == 2
    assert result.persisted_items == 1
    assert len(result.activations) == 1
    with Session(postgres_engine) as session:
        match = session.scalar(select(ChinaAnnouncementMatchModel))
        assert match is not None
        assert match.hit_count == 2
        assert match.matched_search_keywords == ["回购", "中标"]
        assert match.query_stock_code is None
        assert (
            session.scalar(select(func.count()).select_from(ChinaAnnouncementModel))
            == 1
        )
        assert session.scalar(select(func.count()).select_from(ChinaSummaryModel)) == 1


def test_filtered_match_never_creates_summary_delivery_or_activation(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    item = _provider_item("filtered-1", title="关于更正公告")
    provider = _FakeProvider(
        "cninfo", {("688090", None): _query_result("cninfo", item)}
    )
    stage = _stage(
        routes=AnnouncementProviderRoutes(),
        providers={"cninfo": provider},
        uow_factory=lambda: UnitOfWork(session_factory),
    )

    result = stage.execute(
        (
            _task(
                plan_key="filtered-plan",
                excluded=("更正",),
                target=_target(),
            ),
        )
    )

    assert result.filtered_matches == 1
    assert result.activations == ()
    with Session(postgres_engine) as session:
        match = session.scalar(select(ChinaAnnouncementMatchModel))
        assert match is not None and match.filter_status == "filtered"
        assert session.scalar(select(func.count()).select_from(ChinaSummaryModel)) == 0
        assert (
            session.scalar(select(func.count()).select_from(TelegramDeliveryModel)) == 0
        )


class _FailFirstCommitSession(Session):
    failures_remaining = 1

    def commit(self) -> None:
        if type(self).failures_remaining:
            type(self).failures_remaining -= 1
            raise RuntimeError("forced commit failure")
        super().commit()


def test_commit_failure_does_not_merge_stats_and_next_item_still_commits(
    postgres_engine: Engine,
) -> None:
    _FailFirstCommitSession.failures_remaining = 1
    session_factory = sessionmaker(
        bind=postgres_engine,
        class_=_FailFirstCommitSession,
        expire_on_commit=False,
    )
    provider = _FakeProvider(
        "cninfo",
        {
            ("688090", None): _query_result(
                "cninfo",
                _provider_item("commit-fails"),
                _provider_item("commit-succeeds"),
            )
        },
    )
    stage = _stage(
        routes=AnnouncementProviderRoutes(),
        providers={"cninfo": provider},
        uow_factory=lambda: UnitOfWork(session_factory),
    )

    result = stage.execute((_task(plan_key="commit-plan"),))

    assert result.queries_succeeded == 1
    assert result.persisted_items == 1
    assert result.created_matches == 1
    assert len(result.errors) == 1
    assert result.errors[0].phase == "persist"
    assert "forced commit failure" in result.errors[0].message
    with Session(postgres_engine) as session:
        identities = session.scalars(
            select(ChinaAnnouncementModel.provider_announcement_id)
        ).all()
        assert identities == ["commit-succeeds"]


def test_explicit_provider_failure_does_not_fallback_and_other_query_commits(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    cninfo = _FakeProvider("cninfo", {})
    sse = _FakeProvider("sse", {("688090", None): RuntimeError("SSE unavailable")})
    szse_item = _provider_item("szse-survives", provider_key="szse", exchange="sz")
    szse = _FakeProvider("szse", {("000510", None): _query_result("szse", szse_item)})
    stage = _stage(
        routes=AnnouncementProviderRoutes(sh="sse", sz="szse"),
        providers={"cninfo": cninfo, "sse": sse, "szse": szse},
        uow_factory=lambda: UnitOfWork(session_factory),
    )

    result = stage.execute(
        (
            _task(
                plan_key="no-fallback",
                provider_key="sse",
                exchange="sh",
                stock_code="688090",
            ),
            _task(
                plan_key="no-fallback",
                provider_key="szse",
                exchange="sz",
                stock_code="000510",
            ),
        )
    )

    assert result.queries_succeeded == 1
    assert result.persisted_items == 1
    assert len(result.errors) == 1
    assert result.errors[0].phase == "query"
    assert result.errors[0].provider_key == "sse"
    assert "SSE unavailable" in result.errors[0].message
    assert len(sse.queries) == 1
    assert len(szse.queries) == 1
    assert cninfo.queries == []
    with Session(postgres_engine) as session:
        announcements = session.scalars(select(ChinaAnnouncementModel)).all()
        assert len(announcements) == 1
        assert announcements[0].provider_key == "szse"
        assert announcements[0].provider_announcement_id == "szse-survives"


@pytest.mark.parametrize(
    ("provider_key", "exchange", "routes", "model"),
    [
        ("cninfo", "sh", AnnouncementProviderRoutes(), CninfoAnnouncementModel),
        (
            "sse",
            "sh",
            AnnouncementProviderRoutes(sh="sse"),
            SseAnnouncementModel,
        ),
        (
            "szse",
            "sz",
            AnnouncementProviderRoutes(sz="szse"),
            SzseAnnouncementModel,
        ),
    ],
)
def test_stage_persists_each_provider_snapshot_with_china_aggregate(
    postgres_engine: Engine,
    provider_key: str,
    exchange: str,
    routes: AnnouncementProviderRoutes,
    model,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    item = _provider_item(
        f"{provider_key}-raw", provider_key=provider_key, exchange=exchange
    )
    stock_code = "000510" if exchange == "sz" else "688090"
    provider = _FakeProvider(
        provider_key,
        {(stock_code, None): _query_result(provider_key, item)},
    )
    stage = _stage(
        routes=routes,
        providers={provider_key: provider},
        uow_factory=lambda: UnitOfWork(session_factory),
    )

    result = stage.execute(
        (
            _task(
                plan_key=f"{provider_key}-plan",
                provider_key=provider_key,
                exchange=exchange,
                stock_code=stock_code,
            ),
        )
    )

    assert result.persisted_items == 1
    with Session(postgres_engine) as session:
        assert session.scalar(select(func.count()).select_from(model)) == 1
        assert (
            session.scalar(select(func.count()).select_from(ChinaAnnouncementModel))
            == 1
        )


def test_new_plan_on_completed_summary_materializes_new_delivery_in_same_uow(
    postgres_engine: Engine,
) -> None:
    session_factory = create_session_factory(postgres_engine)
    item = _provider_item("shared-summary")
    provider = _FakeProvider(
        "cninfo", {("688090", None): _query_result("cninfo", item)}
    )
    first_stage = _stage(
        routes=AnnouncementProviderRoutes(),
        providers={"cninfo": provider},
        uow_factory=lambda: UnitOfWork(session_factory),
    )
    first = first_stage.execute((_task(plan_key="plan-alpha"),))
    summary_id = first.activations[0].summary_id
    with UnitOfWork(session_factory) as uow:
        claim = uow.china_summaries.claim_next()
        assert claim is not None and claim.summary.id == summary_id
    with UnitOfWork(session_factory) as uow:
        uow.china_summaries.lock(summary_id)
        uow.china_summaries.save_completed(summary_id, summary_completion())

    materialized: list[tuple[int, int]] = []

    def materializer(uow: UnitOfWork, locked_summary_id: int, delivery_id: int) -> None:
        materialized.append((locked_summary_id, delivery_id))
        uow.telegram.insert_summary_message(
            TelegramSummaryMessageWrite(
                telegram_delivery_id=delivery_id,
                text_content="已完成摘要的冻结消息",
            )
        )

    second_stage = _stage(
        routes=AnnouncementProviderRoutes(),
        providers={"cninfo": provider},
        uow_factory=lambda: UnitOfWork(session_factory),
        materializer=materializer,
    )
    second = second_stage.execute((_task(plan_key="plan-beta", target=_target()),))

    assert len(second.activations) == 1
    assert second.activations[0].summary_id == summary_id
    assert second.activations[0].delivery_id is not None
    assert materialized == [(summary_id, second.activations[0].delivery_id)]
    with Session(postgres_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(ChinaAnnouncementModel))
            == 1
        )
        assert session.scalar(select(func.count()).select_from(ChinaSummaryModel)) == 1
        assert (
            session.scalar(
                select(func.count()).select_from(ChinaAnnouncementMatchModel)
            )
            == 2
        )
        assert (
            session.scalar(select(func.count()).select_from(TelegramDeliveryModel)) == 1
        )
        assert (
            session.scalar(
                select(func.count()).select_from(TelegramSummaryMessageModel)
            )
            == 1
        )


def test_invalid_target_fails_before_provider_query(postgres_engine: Engine) -> None:
    provider = _FakeProvider("cninfo", {})

    def invalid_target(_url: str) -> TelegramTarget:
        raise ValueError("invalid Telegram target")

    stage = _stage(
        routes=AnnouncementProviderRoutes(),
        providers={"cninfo": provider},
        uow_factory=lambda: UnitOfWork(create_session_factory(postgres_engine)),
        target_parser=invalid_target,
    )

    with pytest.raises(ValueError, match="invalid Telegram target"):
        stage.execute((_task(plan_key="invalid-target", target=_target()),))

    assert provider.queries == []
