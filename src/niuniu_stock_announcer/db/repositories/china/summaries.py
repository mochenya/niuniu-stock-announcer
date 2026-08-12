"""China 公告摘要任务 Repository。"""

from datetime import datetime
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from niuniu_stock_announcer.db.errors import (
    InvalidStateTransitionError,
    RecordNotFoundError,
)
from niuniu_stock_announcer.db.model.china import (
    ChinaAnnouncementMatchModel,
    ChinaAnnouncementModel,
    ChinaSummaryModel,
)
from niuniu_stock_announcer.db.repositories.china.matches import _map_match
from niuniu_stock_announcer.db.repositories.china._mapping import (
    map_china_announcement,
    map_china_summary,
)
from niuniu_stock_announcer.db.schema import (
    ChinaSummaryClaim,
    ChinaSummaryRecord,
    ChinaSummaryRenderContext,
    SummaryCompletion,
)


class ChinaSummaryRepository:
    """管理公告级唯一摘要任务、原子领取与显式恢复。"""

    def __init__(self, session: Session) -> None:
        """绑定当前 UnitOfWork Session。

        Args:
            session: 当前短事务唯一使用的 Session。
        """
        self._session = session
        self._locked_summary_ids: set[int] = set()

    def ensure(self, announcement_id: int) -> ChinaSummaryRecord:
        """为公告创建唯一 pending 摘要任务或返回已有任务。

        Args:
            announcement_id: China 公告内部 ID。

        Returns:
            公告级唯一摘要任务。
        """
        model = self._session.scalars(
            insert(ChinaSummaryModel)
            .values(china_announcement_id=announcement_id)
            .on_conflict_do_nothing(index_elements=["china_announcement_id"])
            .returning(ChinaSummaryModel)
        ).one_or_none()
        if model is None:
            model = self._session.scalar(
                select(ChinaSummaryModel).where(
                    ChinaSummaryModel.china_announcement_id == announcement_id
                )
            )
        if model is None:
            raise RecordNotFoundError("摘要冲突后未找到已存在任务")
        return map_china_summary(model)

    def get(self, summary_id: int) -> ChinaSummaryRecord | None:
        """按内部 ID 读取摘要任务。

        Args:
            summary_id: China 摘要内部 ID。

        Returns:
            找到时返回冻结记录，否则返回 `None`。
        """
        model = self._session.get(ChinaSummaryModel, summary_id)
        return None if model is None else map_china_summary(model)

    def lock(self, summary_id: int) -> ChinaSummaryRecord:
        """锁定统一 summary 行，作为 delivery/terminal 物化顺序的起点。

        新 delivery 与摘要终态都必须先调用本方法。若绕过该锁，两条并发路径可能彼此看不见
        未提交 parent，最终留下永久缺失的 child message。

        Args:
            summary_id: China 摘要内部 ID。

        Returns:
            当前冻结摘要记录。

        Raises:
            RecordNotFoundError: 摘要任务不存在。
        """
        model = self._session.scalar(
            select(ChinaSummaryModel)
            .where(ChinaSummaryModel.id == summary_id)
            .with_for_update()
        )
        if model is None:
            raise RecordNotFoundError(f"China 摘要不存在: {summary_id}")
        self._locked_summary_ids.add(summary_id)
        return map_china_summary(model)

    def get_render_context(self, summary_id: int) -> ChinaSummaryRenderContext:
        """读取 Delivery Service 所需的 China 公告、摘要和 selected match。

        Args:
            summary_id: China 摘要内部 ID。

        Returns:
            完全脱离 ORM Session 的 China 侧渲染上下文。

        Raises:
            RecordNotFoundError: 摘要或其公告不存在。
        """
        summary = self._session.get(ChinaSummaryModel, summary_id)
        if summary is None:
            raise RecordNotFoundError(f"China 摘要不存在: {summary_id}")
        announcement = self._session.get(
            ChinaAnnouncementModel, summary.china_announcement_id
        )
        if announcement is None:
            raise RecordNotFoundError("摘要关联的 China 公告不存在")
        matches = self._session.scalars(
            select(ChinaAnnouncementMatchModel)
            .where(
                ChinaAnnouncementMatchModel.china_announcement_id == announcement.id,
                ChinaAnnouncementMatchModel.filter_status == "selected",
            )
            .order_by(ChinaAnnouncementMatchModel.id)
        )
        return ChinaSummaryRenderContext(
            summary=map_china_summary(summary),
            announcement=map_china_announcement(announcement),
            selected_matches=tuple(_map_match(model) for model in matches),
        )

    def claim_next(
        self, *, mode: Literal["pending", "failed"] = "pending"
    ) -> ChinaSummaryClaim | None:
        """原子领取一条摘要任务并立即转为 running。

        Args:
            mode: `pending` 服务普通恢复，`failed` 只服务显式 retry。

        Returns:
            领取成功返回脱离 Session 的摘要与公告，队列为空返回 `None`。
        """
        candidate = (
            select(ChinaSummaryModel.id)
            .where(ChinaSummaryModel.status == mode)
            .order_by(ChinaSummaryModel.created_at, ChinaSummaryModel.id)
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        model = self._session.scalars(
            update(ChinaSummaryModel)
            .where(ChinaSummaryModel.id == candidate)
            .values(
                status="running",
                started_at=func.now(),
                finished_at=None,
                failure_reason=None,
                failure_log=None,
                updated_at=func.now(),
            )
            .returning(ChinaSummaryModel)
        ).one_or_none()
        if model is None:
            return None
        announcement = self._session.get(
            ChinaAnnouncementModel, model.china_announcement_id
        )
        if announcement is None:
            raise RecordNotFoundError("摘要关联的 China 公告不存在")
        return ChinaSummaryClaim(
            summary=map_china_summary(model),
            announcement=map_china_announcement(announcement),
        )

    def save_completed(
        self, summary_id: int, completion: SummaryCompletion
    ) -> ChinaSummaryRecord:
        """把已锁定 running 摘要保存为 completed。

        调用方必须在本事务先 `lock(summary_id)`，并在返回后、提交前为全部 delivery 物化
        child payload，才能保证终态与 outbox 原子一致。

        Args:
            summary_id: 当前 running 摘要 ID。
            completion: Agent 审计字段与权威结果。

        Returns:
            completed 冻结记录。

        Raises:
            InvalidStateTransitionError: 未先锁定摘要，或摘要不是 running。
        """
        self._require_locked(summary_id)
        result = completion.result.model_dump(mode="json")
        model = self._session.scalars(
            update(ChinaSummaryModel)
            .where(
                ChinaSummaryModel.id == summary_id,
                ChinaSummaryModel.status == "running",
            )
            .values(
                status="completed",
                agent_key=completion.agent_key,
                agent_version=completion.agent_version,
                prompt_version=completion.prompt_version,
                model_provider=completion.model_provider,
                model_name=completion.model_name,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                summary_text=completion.result.summary_text,
                summary_tags=list(completion.result.summary_tags),
                summary_result=result,
                failure_reason=None,
                failure_log=None,
                finished_at=func.now(),
                updated_at=func.now(),
            )
            .returning(ChinaSummaryModel)
        ).one_or_none()
        if model is None:
            raise InvalidStateTransitionError("只有 running 摘要可以保存成功结果")
        return map_china_summary(model)

    def save_failed(
        self, summary_id: int, *, reason: str, failure_log: str
    ) -> ChinaSummaryRecord:
        """记录一次确定摘要失败并增加失败计数。

        Args:
            summary_id: 当前 running 摘要 ID。
            reason: 简短且不含秘密的失败原因。
            failure_log: 受控诊断文本。

        Returns:
            failed 冻结记录。

        Raises:
            InvalidStateTransitionError: 摘要不是 running。
        """
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("reason 不能为空")
        model = self._session.scalars(
            update(ChinaSummaryModel)
            .where(
                ChinaSummaryModel.id == summary_id,
                ChinaSummaryModel.status == "running",
            )
            .values(
                status="failed",
                failure_count=ChinaSummaryModel.failure_count + 1,
                failure_reason=normalized_reason,
                failure_log=failure_log,
                finished_at=func.now(),
                updated_at=func.now(),
            )
            .returning(ChinaSummaryModel)
        ).one_or_none()
        if model is None:
            raise InvalidStateTransitionError("只有 running 摘要可以记录失败")
        return map_china_summary(model)

    def save_skipped(
        self, summary_id: int, *, reason: str, minimum_failure_count: int
    ) -> ChinaSummaryRecord:
        """把重试耗尽且具备 PDF 的摘要保存为显式降级终态。

        是否具备已验证 PDF 由本 SQL 联表再次保证，不能只信任调用方早先读取的候选快照。

        Args:
            summary_id: 当前 failed 摘要 ID。
            reason: 面向降级消息的简短原因。
            minimum_failure_count: AppSettings 冻结的摘要重试耗尽阈值。

        Returns:
            skipped 冻结记录。

        Raises:
            InvalidStateTransitionError: 未耗尽重试、状态不对或没有完整 PDF 快照。
        """
        if minimum_failure_count <= 0:
            raise ValueError("minimum_failure_count 必须大于 0")
        self._require_locked(summary_id)
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("reason 不能为空")
        has_pdf = select(ChinaAnnouncementModel.id).where(
            ChinaAnnouncementModel.id == ChinaSummaryModel.china_announcement_id,
            ChinaAnnouncementModel.pdf_storage_relative_path.is_not(None),
            ChinaAnnouncementModel.pdf_size_bytes.is_not(None),
            ChinaAnnouncementModel.pdf_sha256.is_not(None),
        )
        model = self._session.scalars(
            update(ChinaSummaryModel)
            .where(
                ChinaSummaryModel.id == summary_id,
                ChinaSummaryModel.status == "failed",
                ChinaSummaryModel.failure_count >= minimum_failure_count,
                has_pdf.exists(),
            )
            .values(
                status="skipped",
                failure_reason=normalized_reason,
                failure_log=None,
                finished_at=func.now(),
                updated_at=func.now(),
            )
            .returning(ChinaSummaryModel)
        ).one_or_none()
        if model is None:
            raise InvalidStateTransitionError(
                "只有重试耗尽且具备已验证 PDF 的 failed 摘要可以降级为 skipped"
            )
        return map_china_summary(model)

    def recover_stale_running(self, *, started_before: datetime) -> int:
        """把过期 running 摘要恢复为可显式 retry 的 failed。

        Args:
            started_before: 早于该时刻仍 running 的任务视为 stale。

        Returns:
            被恢复的摘要数量。
        """
        result = self._session.execute(
            update(ChinaSummaryModel)
            .where(
                ChinaSummaryModel.status == "running",
                ChinaSummaryModel.started_at < started_before,
            )
            .values(
                status="failed",
                failure_count=ChinaSummaryModel.failure_count + 1,
                failure_reason="stale running summary recovered",
                failure_log="stale running summary recovered before external result",
                finished_at=func.now(),
                updated_at=func.now(),
            )
        )
        return result.rowcount

    def _require_locked(self, summary_id: int) -> None:
        if summary_id not in self._locked_summary_ids:
            raise InvalidStateTransitionError(
                "保存摘要终态前必须先锁定同一 china_summaries 行"
            )
