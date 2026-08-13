"""China Pipeline 的编排边界测试。"""

from __future__ import annotations

from datetime import date

from niuniu_stock_announcer.pipelines.china.pipeline import ChinaPipeline
from niuniu_stock_announcer.pipelines.china.profile import ChinaMarketProfile
from niuniu_stock_announcer.pipelines.china.schema import SelectedStocksPlan
from niuniu_stock_announcer.pipelines.china.stages.delivery import DeliveryStageResult
from niuniu_stock_announcer.pipelines.china.stages.summary import SummaryStageResult
from niuniu_stock_announcer.pipelines.china.discovery.schema import (
    SyncActivation,
    SyncResult,
)


class _FakeSyncStage:
    def __init__(self, result: SyncResult) -> None:
        self.result = result
        self.tasks = ()

    def execute(self, tasks) -> SyncResult:
        self.tasks = tuple(tasks)
        return self.result


class _FakeSummaryStage:
    def __init__(self) -> None:
        self.summary_ids = None

    def execute(self, *, summary_ids=None, limit=None) -> SummaryStageResult:
        self.summary_ids = summary_ids
        return SummaryStageResult(completed_count=1)


class _FakeDeliveryStage:
    def __init__(self) -> None:
        self.delivery_ids = None

    def execute(self, *, delivery_ids=None, limit=None) -> DeliveryStageResult:
        self.delivery_ids = delivery_ids
        return DeliveryStageResult(sent_count=1)


def _plan() -> SelectedStocksPlan:
    return SelectedStocksPlan.model_validate(
        {
            "market": "china",
            "plan_type": "selected_stocks",
            "plan_key": "selected-plan",
            "window_days": 3,
            "market_scopes": {
                "a_share": {"stocks": [{"exchange": "sh", "stock_code": "688090"}]}
            },
        }
    )


def test_run_uses_new_selected_activations_not_announcement_count() -> None:
    activation = SyncActivation(
        announcement_id=11,
        match_id=21,
        summary_id=31,
        delivery_id=41,
    )
    sync_stage = _FakeSyncStage(SyncResult(activations=(activation,)))
    summary_stage = _FakeSummaryStage()
    delivery_stage = _FakeDeliveryStage()
    pipeline = ChinaPipeline(
        _plan(),
        profile=ChinaMarketProfile(),
        sync_stage=sync_stage,
        summary_stage=summary_stage,
        delivery_stage=delivery_stage,
        today=lambda: date(2026, 8, 13),
    )

    result = pipeline.run(limit=7)

    assert result.sync.activations == (activation,)
    assert summary_stage.summary_ids == (31,)
    assert delivery_stage.delivery_ids == (41,)
    assert sync_stage.tasks[0].query.start_date == date(2026, 8, 12)
    assert sync_stage.tasks[0].query.end_date == date(2026, 8, 14)


def test_sync_only_compiles_and_executes_discovery() -> None:
    sync_stage = _FakeSyncStage(SyncResult())
    pipeline = ChinaPipeline(
        _plan(),
        profile=ChinaMarketProfile(),
        sync_stage=sync_stage,
        summary_stage=None,
        delivery_stage=None,
        today=lambda: date(2026, 8, 13),
    )

    assert pipeline.sync(limit=2) == SyncResult()
    assert len(sync_stage.tasks) == 1
    assert sync_stage.tasks[0].query.limit == 2
