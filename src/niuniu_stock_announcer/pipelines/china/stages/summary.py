"""China 摘要领取、外部副作用与终态事务编排。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from niuniu_stock_announcer.announcements.schema import (
    AnnouncementSecurity,
    ChinaAnnouncement,
    StoredAnnouncementDocument,
)
from niuniu_stock_announcer.db.schema import (
    ChinaSummaryClaim,
    ChinaSummaryRecord,
    PdfSnapshot,
)
from niuniu_stock_announcer.db.unit_of_work import UnitOfWork
from niuniu_stock_announcer.summary.errors import serialize_summary_error
from niuniu_stock_announcer.summary.schema import SummaryCompletion

SummaryExecutionMode = Literal["pending", "failed"]
SummaryErrorPhase = Literal[
    "claim",
    "document",
    "pdf_persist",
    "agent",
    "terminal",
    "failure_persist",
    "skip",
]
UnitOfWorkFactory = Callable[[], AbstractContextManager[UnitOfWork]]
TerminalDeliveryMaterializer = Callable[[UnitOfWork, int, int], None]


class SummaryCapability(Protocol):
    """定义 SummaryStage 在事务外需要的 PDF 与 Agent 能力。"""

    def ensure_pdf(self, announcement: ChinaAnnouncement) -> StoredAnnouncementDocument:
        """下载或复用一份已验证 PDF。

        Args:
            announcement: 领取事务提交后的冻结公告事实。

        Returns:
            已验证且位于 storage root 内的文档快照。
        """

    def summarize_document(
        self,
        announcement: ChinaAnnouncement,
        document: StoredAnnouncementDocument,
    ) -> SummaryCompletion:
        """提取文档内容并调用市场 Agent。

        Args:
            announcement: 领取事务提交后的冻结公告事实。
            document: 已验证并已持久化身份的 PDF。

        Returns:
            不含完整 LLM response 的版本化摘要结果。
        """


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SummaryStageError(_FrozenSchema):
    """保存一条不含完整 LLM response 的摘要阶段错误。"""

    summary_id: int | None = None
    phase: SummaryErrorPhase
    error_type: str
    message: str


class SummaryStageResult(_FrozenSchema):
    """汇总本次摘要阶段已提交的状态变化。"""

    claimed_count: int = Field(default=0, ge=0)
    completed_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    errors: tuple[SummaryStageError, ...] = ()


class SummaryStage:
    """用多个短 UoW 编排摘要领取、PDF、Agent 与终态物化。"""

    def __init__(
        self,
        summary_service: SummaryCapability,
        uow_factory: UnitOfWorkFactory,
        terminal_delivery_materializer: TerminalDeliveryMaterializer,
        *,
        max_failures: int,
    ) -> None:
        """绑定摘要服务、事务 factory、纯物化器和失败阈值。

        Args:
            summary_service: 不打开事务的 PDF/Agent 能力服务。
            uow_factory: 每次调用返回全新短事务的 factory。
            terminal_delivery_materializer: 在终态事务内创建不可变 child payload。
            max_failures: 显式 retry 转 skipped 的累计失败阈值。
        """
        if max_failures <= 0:
            raise ValueError("max_failures 必须大于 0")
        self._summary_service = summary_service
        self._uow_factory = uow_factory
        self._terminal_delivery_materializer = terminal_delivery_materializer
        self._max_failures = max_failures

    def execute(
        self,
        *,
        mode: SummaryExecutionMode = "pending",
        summary_ids: Sequence[int] | None = None,
        limit: int | None = None,
    ) -> SummaryStageResult:
        """处理 pending 或显式 failed 摘要，外部调用期间不持有 Session。

        Args:
            mode: `pending` 普通处理，`failed` 只服务显式 retry。
            summary_ids: 可选摘要 ID 白名单；空序列表示无工作。
            limit: 本轮最多提交的 claim/skip 数量。

        Returns:
            只统计成功提交状态变化的阶段结果与受控错误。

        Raises:
            ValueError: ID 或 limit 不符合调用合同。
        """
        if mode not in {"pending", "failed"}:
            raise ValueError("mode 只能是 pending 或 failed")
        normalized_ids = _normalize_summary_ids(summary_ids)
        if limit is not None and limit <= 0:
            raise ValueError("limit 必须大于 0")
        claimed_count = 0
        completed_count = 0
        failed_count = 0
        skipped_count = 0
        errors: list[SummaryStageError] = []
        processed_count = 0
        processed_ids: set[int] = set()

        while limit is None or processed_count < limit:
            if mode == "failed":
                try:
                    skipped_id = self._skip_next_exhausted(
                        normalized_ids, processed_ids
                    )
                except Exception as exc:
                    errors.append(_stage_error(None, "skip", exc))
                    break
                if skipped_id is not None:
                    processed_ids.add(skipped_id)
                    skipped_count += 1
                    processed_count += 1
                    continue

            try:
                claim = self._claim_next(mode, normalized_ids, processed_ids)
            except Exception as exc:
                errors.append(_stage_error(None, "claim", exc))
                # 领取提交失败时同一 pending 行仍可能排在队首，停止以免忙循环。
                break
            if claim is None:
                break

            claimed_count += 1
            processed_count += 1
            processed_ids.add(claim.summary.id)
            outcome, error = self._process_claim(claim, mode=mode)
            if outcome == "completed":
                completed_count += 1
            elif outcome == "skipped":
                skipped_count += 1
            elif outcome == "failed":
                failed_count += 1
            if error is not None:
                errors.append(error)

        return SummaryStageResult(
            claimed_count=claimed_count,
            completed_count=completed_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            errors=tuple(errors),
        )

    def recover_stale(self, *, started_before: datetime) -> int:
        """把超时 running 摘要恢复为只可显式 retry 的 failed。

        Args:
            started_before: 早于该时间仍 running 的记录视为 stale。

        Returns:
            成功提交的恢复记录数量。
        """
        with self._uow_factory() as uow:
            return uow.china_summaries.recover_stale_running(
                started_before=started_before
            )

    def _claim_next(
        self,
        mode: SummaryExecutionMode,
        summary_ids: tuple[int, ...] | None,
        processed_ids: set[int],
    ) -> ChinaSummaryClaim | None:
        with self._uow_factory() as uow:
            return uow.china_summaries.claim_next(
                mode=mode,
                summary_ids=summary_ids,
                excluded_summary_ids=processed_ids,
                maximum_failure_count=(
                    self._max_failures if mode == "failed" else None
                ),
            )

    def _skip_next_exhausted(
        self,
        summary_ids: tuple[int, ...] | None,
        processed_ids: set[int],
    ) -> int | None:
        with self._uow_factory() as uow:
            exhausted = uow.china_summaries.lock_next_exhausted(
                minimum_failure_count=self._max_failures,
                summary_ids=summary_ids,
                excluded_summary_ids=processed_ids,
            )
            if exhausted is None:
                return None
            uow.china_summaries.save_skipped(
                exhausted.id,
                reason="摘要重试次数已达到上限",
                minimum_failure_count=self._max_failures,
            )
            self._materialize_all_deliveries(uow, exhausted.id)
            return exhausted.id

    def _process_claim(
        self, claim: ChinaSummaryClaim, *, mode: SummaryExecutionMode
    ) -> tuple[
        Literal["completed", "failed", "skipped", "unresolved"],
        SummaryStageError | None,
    ]:
        summary_id = claim.summary.id
        phase: SummaryErrorPhase = "document"
        pdf_ready = False
        try:
            announcement = _announcement_from_record(claim)
            document = self._summary_service.ensure_pdf(announcement)
            phase = "pdf_persist"
            with self._uow_factory() as uow:
                uow.china_announcements.attach_pdf(
                    claim.announcement.id,
                    PdfSnapshot(
                        storage_relative_path=document.storage_relative_path,
                        size_bytes=document.size_bytes,
                        sha256=document.sha256,
                    ),
                )
            pdf_ready = True
            phase = "agent"
            completion = self._summary_service.summarize_document(
                announcement, document
            )
            phase = "terminal"
            with self._uow_factory() as uow:
                uow.china_summaries.lock(summary_id)
                uow.china_summaries.save_completed(summary_id, completion)
                self._materialize_all_deliveries(uow, summary_id)
            return "completed", None
        except Exception as exc:
            original_error = _stage_error(summary_id, phase, exc)
            try:
                failed = self._save_failed(summary_id, exc)
            except Exception as persist_exc:
                return "unresolved", _stage_error(
                    summary_id,
                    "failure_persist",
                    persist_exc,
                    context=original_error.message,
                )
            if (
                mode == "failed"
                and phase == "agent"
                and pdf_ready
                and failed.failure_count >= self._max_failures
            ):
                try:
                    self._skip_summary(summary_id)
                except Exception as skip_exc:
                    return "failed", _stage_error(summary_id, "skip", skip_exc)
                return "skipped", original_error
            return "failed", original_error

    def _save_failed(self, summary_id: int, exc: Exception) -> ChinaSummaryRecord:
        reason = serialize_summary_error(exc)
        failure_log = f"{exc.__class__.__name__}: {reason}"
        with self._uow_factory() as uow:
            return uow.china_summaries.save_failed(
                summary_id,
                reason=reason,
                failure_log=failure_log,
            )

    def _skip_summary(self, summary_id: int) -> None:
        with self._uow_factory() as uow:
            uow.china_summaries.lock(summary_id)
            uow.china_summaries.save_skipped(
                summary_id,
                reason="摘要重试次数已达到上限",
                minimum_failure_count=self._max_failures,
            )
            self._materialize_all_deliveries(uow, summary_id)

    def _materialize_all_deliveries(self, uow: UnitOfWork, summary_id: int) -> None:
        for delivery in uow.telegram.list_deliveries(
            producer_key="china_summary", business_key=str(summary_id)
        ):
            self._terminal_delivery_materializer(uow, summary_id, delivery.id)


def _announcement_from_record(claim: ChinaSummaryClaim) -> ChinaAnnouncement:
    value = claim.announcement
    securities = tuple(
        AnnouncementSecurity(
            exchange=exchange,
            stock_code=stock_code,
            stock_name=stock_name,
        )
        for exchange, stock_code, stock_name in zip(
            value.exchanges,
            value.stock_codes,
            value.stock_names,
            strict=True,
        )
    )
    return ChinaAnnouncement(
        provider_key=value.provider_key,
        provider_announcement_id=value.provider_announcement_id,
        market_scope=value.market_scope,
        securities=securities,
        title=value.title,
        published_at=value.published_at,
        source_url=value.source_url,
    )


def _normalize_summary_ids(
    summary_ids: Sequence[int] | None,
) -> tuple[int, ...] | None:
    if summary_ids is None:
        return None
    normalized = tuple(dict.fromkeys(summary_ids))
    if any(summary_id <= 0 for summary_id in normalized):
        raise ValueError("summary_ids 必须全部大于 0")
    return normalized


def _stage_error(
    summary_id: int | None,
    phase: SummaryErrorPhase,
    exc: Exception,
    *,
    context: str | None = None,
) -> SummaryStageError:
    message = serialize_summary_error(exc)
    if context is not None:
        message = f"{message}; original={context}"
    return SummaryStageError(
        summary_id=summary_id,
        phase=phase,
        error_type=exc.__class__.__name__,
        message=message,
    )
