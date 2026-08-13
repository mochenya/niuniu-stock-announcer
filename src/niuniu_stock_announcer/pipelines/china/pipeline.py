"""China discovery、摘要与投递的市场级执行拓扑。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

from pydantic import BaseModel, ConfigDict

from niuniu_stock_announcer.pipelines.china.discovery.market_keywords import (
    compile_market_keyword_tasks,
)
from niuniu_stock_announcer.pipelines.china.discovery.schema import SyncResult
from niuniu_stock_announcer.pipelines.china.discovery.selected_stocks import (
    compile_selected_stock_tasks,
)
from niuniu_stock_announcer.pipelines.china.profile import ChinaMarketProfile
from niuniu_stock_announcer.pipelines.china.schema import (
    ChinaPlan,
    MarketKeywordsPlan,
    SelectedStocksPlan,
)
from niuniu_stock_announcer.pipelines.china.stages.delivery import (
    DeliveryStage,
    DeliveryStageResult,
)
from niuniu_stock_announcer.pipelines.china.stages.summary import (
    SummaryStage,
    SummaryStageResult,
)
from niuniu_stock_announcer.pipelines.china.stages.sync import SyncStage


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChinaPipelineResult(_FrozenSchema):
    """保存一次 China Pipeline 的同步、摘要和投递结果。"""

    sync: SyncResult
    summary: SummaryStageResult
    delivery: DeliveryStageResult


class ChinaPipeline:
    """按一个固定 Plan 编排 discovery、摘要和 Telegram Stage。"""

    def __init__(
        self,
        plan: ChinaPlan,
        *,
        profile: ChinaMarketProfile,
        sync_stage: SyncStage,
        summary_stage: SummaryStage | None = None,
        delivery_stage: DeliveryStage | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        """绑定单一 Plan 与已由 composition root 注入的 Stage。

        Args:
            plan: 本次命令唯一加载的冻结 China Plan。
            profile: 提供 scope/exchange 拓扑的 China profile。
            sync_stage: 负责 Provider 查询与 discovery 持久化的 Stage。
            summary_stage: 负责本轮摘要状态机的 Stage。
            delivery_stage: 负责本轮 Telegram child 状态机的 Stage。
            today: 可注入日期函数，测试时避免依赖系统日期。
        """
        self._plan = plan
        self._profile = profile
        self._sync_stage = sync_stage
        self._summary_stage = summary_stage
        self._delivery_stage = delivery_stage
        self._today = today

    @property
    def plan_key(self) -> str:
        """返回当前 Pipeline 的稳定 Plan key。"""
        return self._plan.plan_key

    def sync(self, *, limit: int | None = None) -> SyncResult:
        """执行当前 Plan 的 discovery，不领取摘要或 Telegram child。

        Args:
            limit: 可选 Provider 单查询结果上限，不改变 Plan 身份。

        Returns:
            SyncStage 提交成功的统计与本轮新激活引用。
        """
        tasks = self._compile_tasks(limit=limit)
        return self._sync_stage.execute(tasks)

    def run(self, *, limit: int | None = None) -> ChinaPipelineResult:
        """同步当前 Plan，并只处理本轮新 selected match 的激活记录。

        Args:
            limit: 可选本轮查询与后处理上限，不改变持久化身份。

        Returns:
            同步、摘要和投递三个 Stage 的冻结结果。
        """
        sync_result = self.sync(limit=limit)
        if self._summary_stage is None:
            raise RuntimeError("run 需要摘要 Stage")
        summary_ids = _unique(item.summary_id for item in sync_result.activations)
        summary_result = self._summary_stage.execute(
            summary_ids=summary_ids,
            limit=limit,
        )
        delivery_ids = _unique(
            item.delivery_id
            for item in sync_result.activations
            if item.delivery_id is not None
        )
        delivery_result = (
            DeliveryStageResult()
            if self._delivery_stage is None
            else self._delivery_stage.execute(
                delivery_ids=delivery_ids,
                limit=limit,
            )
        )
        return ChinaPipelineResult(
            sync=sync_result,
            summary=summary_result,
            delivery=delivery_result,
        )

    def _compile_tasks(self, *, limit: int | None) -> tuple:
        end_date = self._today() + timedelta(days=1)
        if isinstance(self._plan, SelectedStocksPlan):
            return compile_selected_stock_tasks(
                self._plan,
                end_date=end_date,
                limit=limit,
            )
        if isinstance(self._plan, MarketKeywordsPlan):
            return compile_market_keyword_tasks(
                self._plan,
                end_date=end_date,
                profile=self._profile,
                limit=limit,
            )
        raise TypeError(f"不支持的 China Plan 类型: {type(self._plan).__name__}")


def _unique(values):
    return tuple(dict.fromkeys(value for value in values if value is not None))
