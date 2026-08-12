"""China Telegram child 消息的可靠领取与副作用编排。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from niuniu_stock_announcer.db.schema import (
    StaleTelegramRecovery,
    TelegramDocumentClaim,
    TelegramSummaryClaim,
)
from niuniu_stock_announcer.db.unit_of_work import UnitOfWork
from niuniu_stock_announcer.im.telegram.schema import (
    TelegramDocumentSendRequest,
    TelegramSendResult,
    TelegramTarget,
    TelegramTextSendRequest,
)
from niuniu_stock_announcer.im.telegram.sender import (
    TelegramSendFailed,
    TelegramSendOutcomeUnknown,
)

DeliveryExecutionMode = Literal["pending", "failed"]
DeliveryMessageKind = Literal["summary", "document"]
DeliveryErrorPhase = Literal["claim", "send", "terminal"]
UnitOfWorkFactory = Callable[[], AbstractContextManager[UnitOfWork]]


class TelegramSendingCapability(Protocol):
    """定义 DeliveryStage 需要的两种独立 Telegram 发送能力。"""

    def send_text(self, request: TelegramTextSendRequest) -> TelegramSendResult:
        """发送冻结文本并返回已确认结果。"""

    def send_document(self, request: TelegramDocumentSendRequest) -> TelegramSendResult:
        """复验并发送冻结 document，返回已确认结果。"""


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DeliveryStageError(_FrozenSchema):
    """保存不含 token、Plan 或 payload 正文的受控投递错误。"""

    message_id: int | None = None
    message_kind: DeliveryMessageKind | None = None
    phase: DeliveryErrorPhase
    error_type: str
    message: str


class DeliveryStageResult(_FrozenSchema):
    """汇总本轮已提交的 Telegram child 状态变化。"""

    claimed_count: int = Field(default=0, ge=0)
    sent_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    unknown_count: int = Field(default=0, ge=0)
    errors: tuple[DeliveryStageError, ...] = ()


class _MutableResult:
    def __init__(self) -> None:
        self.claimed_count = 0
        self.sent_count = 0
        self.failed_count = 0
        self.unknown_count = 0
        self.errors: list[DeliveryStageError] = []

    def freeze(self) -> DeliveryStageResult:
        return DeliveryStageResult(
            claimed_count=self.claimed_count,
            sent_count=self.sent_count,
            failed_count=self.failed_count,
            unknown_count=self.unknown_count,
            errors=tuple(self.errors),
        )


class DeliveryStage:
    """用短 UoW 编排 child claim、Telegram 调用与逐条结果保存。"""

    def __init__(
        self,
        sender: TelegramSendingCapability,
        uow_factory: UnitOfWorkFactory,
    ) -> None:
        """绑定不读取数据库的 sender 与短事务 factory。

        Args:
            sender: 只接收冻结发送请求的 Telegram adapter。
            uow_factory: 每次调用返回全新 UnitOfWork 的 factory。
        """
        self._sender = sender
        self._uow_factory = uow_factory

    def execute(
        self,
        *,
        mode: DeliveryExecutionMode = "pending",
        delivery_ids: Sequence[int] | None = None,
        limit: int | None = None,
    ) -> DeliveryStageResult:
        """发送 pending 或显式 failed child，且永不领取 unknown。

        Args:
            mode: `pending` 处理普通队列，`failed` 只处理显式重试。
            delivery_ids: 可选 delivery ID 白名单；不提供表示全局恢复。
            limit: 文本与 document 合计最多领取的消息数。

        Returns:
            只统计成功提交的 claim/终态与受控错误。

        Raises:
            ValueError: mode、ID 或 limit 不符合调用合同。
        """
        if mode not in {"pending", "failed"}:
            raise ValueError("mode 只能是 pending 或 failed")
        normalized_delivery_ids = _normalize_delivery_ids(delivery_ids)
        if limit is not None and limit <= 0:
            raise ValueError("limit 必须大于 0")

        result = _MutableResult()
        processed_summary_ids: set[int] = set()
        processed_document_ids: set[int] = set()
        self._drain_summaries(
            mode=mode,
            delivery_ids=normalized_delivery_ids,
            processed_ids=processed_summary_ids,
            limit=limit,
            result=result,
        )
        self._drain_documents(
            mode=mode,
            delivery_ids=normalized_delivery_ids,
            processed_ids=processed_document_ids,
            limit=limit,
            result=result,
        )
        return result.freeze()

    def recover_stale(self, *, started_before: datetime) -> StaleTelegramRecovery:
        """把 stale running Telegram child 统一转成不可自动重试的 unknown。

        Args:
            started_before: 早于该时间仍 running 的 child 视为结果不确定。

        Returns:
            成功提交的文本与 document 恢复数量。
        """
        with self._uow_factory() as uow:
            return uow.telegram.recover_stale_running(started_before=started_before)

    def _drain_summaries(
        self,
        *,
        mode: DeliveryExecutionMode,
        delivery_ids: tuple[int, ...] | None,
        processed_ids: set[int],
        limit: int | None,
        result: _MutableResult,
    ) -> None:
        while limit is None or result.claimed_count < limit:
            try:
                with self._uow_factory() as uow:
                    claim = uow.telegram.claim_next_summary(
                        mode=mode,
                        delivery_ids=delivery_ids,
                        excluded_message_ids=processed_ids,
                    )
            except Exception as exc:
                result.errors.append(_stage_error(None, None, "claim", exc))
                break
            if claim is None:
                break
            result.claimed_count += 1
            processed_ids.add(claim.message.id)
            self._process_summary(claim, result)

    def _drain_documents(
        self,
        *,
        mode: DeliveryExecutionMode,
        delivery_ids: tuple[int, ...] | None,
        processed_ids: set[int],
        limit: int | None,
        result: _MutableResult,
    ) -> None:
        while limit is None or result.claimed_count < limit:
            try:
                with self._uow_factory() as uow:
                    claim = uow.telegram.claim_next_document(
                        mode=mode,
                        delivery_ids=delivery_ids,
                        excluded_message_ids=processed_ids,
                    )
            except Exception as exc:
                result.errors.append(_stage_error(None, None, "claim", exc))
                break
            if claim is None:
                break
            result.claimed_count += 1
            processed_ids.add(claim.message.id)
            self._process_document(claim, result)

    def _process_summary(
        self,
        claim: TelegramSummaryClaim,
        result: _MutableResult,
    ) -> Literal["sent", "failed", "unknown", "unresolved"]:
        try:
            request = TelegramTextSendRequest(
                target=_target_from_claim(claim),
                text_content=claim.message.text_content,
            )
            send_result = self._sender.send_text(request)
        except TelegramSendOutcomeUnknown as exc:
            return self._save_unsent(
                claim.message.id,
                kind="summary",
                status="unknown",
                exc=exc,
                result=result,
            )
        except (TelegramSendFailed, ValueError) as exc:
            return self._save_unsent(
                claim.message.id,
                kind="summary",
                status="failed",
                exc=exc,
                result=result,
            )
        except Exception as exc:
            # 未声明异常可能发生在外部调用已经完成之后；无法证明“未发送”时宁可
            # 隔离为 unknown，也不能自动重试制造重复通知。
            return self._save_unsent(
                claim.message.id,
                kind="summary",
                status="unknown",
                exc=exc,
                result=result,
            )
        try:
            self._save_sent(claim.message.id, kind="summary", value=send_result)
        except Exception as exc:
            result.errors.append(
                _stage_error(claim.message.id, "summary", "terminal", exc)
            )
            return "unresolved"
        result.sent_count += 1
        return "sent"

    def _process_document(
        self,
        claim: TelegramDocumentClaim,
        result: _MutableResult,
    ) -> Literal["sent", "failed", "unknown", "unresolved"]:
        try:
            request = TelegramDocumentSendRequest(
                target=_target_from_claim(claim),
                storage_relative_path=claim.message.storage_relative_path,
                document_filename=claim.message.document_filename,
                document_size_bytes=claim.message.document_size_bytes,
                document_sha256=claim.message.document_sha256,
                document_caption=claim.message.document_caption,
            )
            send_result = self._sender.send_document(request)
        except TelegramSendOutcomeUnknown as exc:
            return self._save_unsent(
                claim.message.id,
                kind="document",
                status="unknown",
                exc=exc,
                result=result,
            )
        except (TelegramSendFailed, ValueError) as exc:
            return self._save_unsent(
                claim.message.id,
                kind="document",
                status="failed",
                exc=exc,
                result=result,
            )
        except Exception as exc:
            # sender 只把发送前可证明的本地校验错误包装为 TelegramSendFailed；
            # 其他意外异常无法排除 Telegram 已收件，因此必须阻止自动重发。
            return self._save_unsent(
                claim.message.id,
                kind="document",
                status="unknown",
                exc=exc,
                result=result,
            )
        try:
            self._save_sent(claim.message.id, kind="document", value=send_result)
        except Exception as exc:
            result.errors.append(
                _stage_error(claim.message.id, "document", "terminal", exc)
            )
            return "unresolved"
        result.sent_count += 1
        return "sent"

    def _save_unsent(
        self,
        message_id: int,
        *,
        kind: DeliveryMessageKind,
        status: Literal["failed", "unknown"],
        exc: Exception,
        result: _MutableResult,
    ) -> Literal["failed", "unknown", "unresolved"]:
        reason = _compact_error(exc)
        failure_log = f"{exc.__class__.__name__}: {reason}"
        persist_exc: Exception | None = None
        for _ in range(2):
            try:
                with self._uow_factory() as uow:
                    save = getattr(uow.telegram, f"save_{kind}_{status}")
                    save(message_id, reason=reason, failure_log=failure_log)
                persist_exc = None
                break
            except Exception as current_exc:
                persist_exc = current_exc
        if persist_exc is not None:
            result.errors.append(
                _stage_error(
                    message_id,
                    kind,
                    "terminal",
                    persist_exc,
                    context=reason,
                )
            )
            return "unresolved"
        if status == "unknown":
            result.unknown_count += 1
        else:
            result.failed_count += 1
        result.errors.append(_stage_error(message_id, kind, "send", exc))
        return status

    def _save_sent(
        self,
        message_id: int,
        *,
        kind: DeliveryMessageKind,
        value: TelegramSendResult,
    ) -> None:
        last_error: Exception | None = None
        for _ in range(2):
            try:
                with self._uow_factory() as uow:
                    save = getattr(uow.telegram, f"save_{kind}_sent")
                    save(
                        message_id,
                        result_chat_id=value.chat_id,
                        result_message_thread_id=value.message_thread_id,
                        telegram_message_id=value.message_id,
                        telegram_message_url=value.message_url,
                    )
                return
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error


def _target_from_claim(
    claim: TelegramSummaryClaim | TelegramDocumentClaim,
) -> TelegramTarget:
    return TelegramTarget(
        chat_id=claim.delivery.target_chat_id,
        message_thread_id=claim.delivery.target_message_thread_id,
    )


def _normalize_delivery_ids(
    delivery_ids: Sequence[int] | None,
) -> tuple[int, ...] | None:
    if delivery_ids is None:
        return None
    normalized = tuple(dict.fromkeys(delivery_ids))
    if any(delivery_id <= 0 for delivery_id in normalized):
        raise ValueError("delivery_ids 必须全部大于 0")
    return normalized


def _compact_error(exc: Exception) -> str:
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    return text[:1000]


def _stage_error(
    message_id: int | None,
    kind: DeliveryMessageKind | None,
    phase: DeliveryErrorPhase,
    exc: Exception,
    *,
    context: str | None = None,
) -> DeliveryStageError:
    message = _compact_error(exc)
    if context is not None:
        message = f"{message}; original={context}"[:1000]
    return DeliveryStageError(
        message_id=message_id,
        message_kind=kind,
        phase=phase,
        error_type=exc.__class__.__name__,
        message=message,
    )
