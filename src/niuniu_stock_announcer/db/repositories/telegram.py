"""共享 Telegram outbox Repository。"""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from niuniu_stock_announcer.db.errors import (
    InvalidStateTransitionError,
    PersistenceConflictError,
    RecordNotFoundError,
)
from niuniu_stock_announcer.db.model.telegram import (
    TelegramDeliveryModel,
    TelegramDocumentMessageModel,
    TelegramSummaryMessageModel,
)
from niuniu_stock_announcer.db.schema import (
    StaleTelegramRecovery,
    TelegramDeliveryRecord,
    TelegramDeliveryWrite,
    TelegramDocumentClaim,
    TelegramDocumentMessageRecord,
    TelegramDocumentMessageWrite,
    TelegramSummaryClaim,
    TelegramSummaryMessageRecord,
    TelegramSummaryMessageWrite,
)


def _map_delivery(model: TelegramDeliveryModel) -> TelegramDeliveryRecord:
    return TelegramDeliveryRecord.model_validate(model)


def _map_summary_message(
    model: TelegramSummaryMessageModel,
) -> TelegramSummaryMessageRecord:
    return TelegramSummaryMessageRecord.model_validate(model)


def _map_document_message(
    model: TelegramDocumentMessageModel,
) -> TelegramDocumentMessageRecord:
    return TelegramDocumentMessageRecord.model_validate(model)


class TelegramRepository:
    """管理不可变 delivery/payload 与两类消息的独立发送状态。"""

    def __init__(self, session: Session) -> None:
        """绑定当前 UnitOfWork Session。

        Args:
            session: 当前短事务唯一使用的 Session。
        """
        self._session = session

    def ensure_delivery(self, value: TelegramDeliveryWrite) -> TelegramDeliveryRecord:
        """创建逻辑投递，冲突时只接受完全相同的 target/document 快照。

        Args:
            value: 已解析 target 并冻结发送意图的逻辑投递。

        Returns:
            新建或已存在的冻结 delivery。

        Raises:
            PersistenceConflictError: 相同业务身份已有不同不可变快照。
        """
        values = value.model_dump(mode="python")
        model = self._session.scalars(
            insert(TelegramDeliveryModel)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[
                    "producer_key",
                    "business_key",
                    "plan_key",
                    "market_scope",
                ]
            )
            .returning(TelegramDeliveryModel)
        ).one_or_none()
        if model is None:
            model = self._session.scalar(
                select(TelegramDeliveryModel).where(
                    TelegramDeliveryModel.producer_key == value.producer_key,
                    TelegramDeliveryModel.business_key == value.business_key,
                    TelegramDeliveryModel.plan_key == value.plan_key,
                    TelegramDeliveryModel.market_scope == value.market_scope,
                )
            )
        if model is None:
            raise RecordNotFoundError("delivery 冲突后未找到已存在记录")
        record = _map_delivery(model)
        if any(
            getattr(record, field) != getattr(value, field)
            for field in (
                "target_key",
                "target_url",
                "target_chat_id",
                "target_message_thread_id",
                "send_original_document",
            )
        ):
            raise PersistenceConflictError(
                "delivery target/document 快照已冻结且不一致"
            )
        return record

    def list_deliveries(
        self, *, producer_key: str, business_key: str
    ) -> tuple[TelegramDeliveryRecord, ...]:
        """列出一个 producer 业务对象的全部逻辑投递。

        Args:
            producer_key: 跨市场 owner namespace。
            business_key: owner 内部业务引用文本。

        Returns:
            按内部 ID 排序的冻结 delivery。
        """
        models = self._session.scalars(
            select(TelegramDeliveryModel)
            .where(
                TelegramDeliveryModel.producer_key == producer_key,
                TelegramDeliveryModel.business_key == business_key,
            )
            .order_by(TelegramDeliveryModel.id)
        )
        return tuple(_map_delivery(model) for model in models)

    def get_delivery(self, delivery_id: int) -> TelegramDeliveryRecord:
        """按内部 ID 读取一条冻结逻辑投递。

        Args:
            delivery_id: Telegram delivery 内部主键。

        Returns:
            脱离 Session 的冻结 delivery。

        Raises:
            RecordNotFoundError: delivery 不存在。
        """
        model = self._session.get(TelegramDeliveryModel, delivery_id)
        if model is None:
            raise RecordNotFoundError(f"Telegram delivery 不存在: {delivery_id}")
        return _map_delivery(model)

    def insert_summary_message(
        self, value: TelegramSummaryMessageWrite
    ) -> TelegramSummaryMessageRecord:
        """只物化一次文本 payload，冲突时禁止重渲染覆盖。

        Args:
            value: 已由纯 Delivery Service 渲染的文本 payload。

        Returns:
            新建或完全相同的已有文本消息。

        Raises:
            PersistenceConflictError: delivery 已有不同文本 payload。
        """
        model = self._session.scalars(
            insert(TelegramSummaryMessageModel)
            .values(**value.model_dump(mode="python"))
            .on_conflict_do_nothing(index_elements=["telegram_delivery_id"])
            .returning(TelegramSummaryMessageModel)
        ).one_or_none()
        if model is None:
            model = self._session.scalar(
                select(TelegramSummaryMessageModel).where(
                    TelegramSummaryMessageModel.telegram_delivery_id
                    == value.telegram_delivery_id
                )
            )
        if model is None:
            raise RecordNotFoundError("summary message 冲突后未找到记录")
        record = _map_summary_message(model)
        if record.text_content != value.text_content:
            raise PersistenceConflictError("Telegram 文本 payload 已冻结且不一致")
        return record

    def insert_document_message(
        self, value: TelegramDocumentMessageWrite
    ) -> TelegramDocumentMessageRecord:
        """只物化一次 document payload，冲突时禁止覆盖路径或文件身份。

        Args:
            value: 已由纯 Delivery Service 渲染的 document payload。

        Returns:
            新建或完全相同的已有 document 消息。

        Raises:
            PersistenceConflictError: 业务身份已有不同 payload。
        """
        model = self._session.scalars(
            insert(TelegramDocumentMessageModel)
            .values(**value.model_dump(mode="python"))
            .on_conflict_do_nothing(
                index_elements=["telegram_delivery_id", "document_key"]
            )
            .returning(TelegramDocumentMessageModel)
        ).one_or_none()
        if model is None:
            model = self._session.scalar(
                select(TelegramDocumentMessageModel).where(
                    TelegramDocumentMessageModel.telegram_delivery_id
                    == value.telegram_delivery_id,
                    TelegramDocumentMessageModel.document_key == value.document_key,
                )
            )
        if model is None:
            raise RecordNotFoundError("document message 冲突后未找到记录")
        record = _map_document_message(model)
        immutable_fields = (
            "source_url",
            "storage_relative_path",
            "document_filename",
            "document_mime_type",
            "document_size_bytes",
            "document_sha256",
            "document_caption",
        )
        if any(
            getattr(record, field) != getattr(value, field)
            for field in immutable_fields
        ):
            raise PersistenceConflictError("Telegram document payload 已冻结且不一致")
        return record

    def claim_next_summary(
        self,
        *,
        mode: Literal["pending", "failed"] = "pending",
        delivery_ids: Sequence[int] | None = None,
        excluded_message_ids: Sequence[int] = (),
    ) -> TelegramSummaryClaim | None:
        """原子领取文本消息；任何路径都不包含 `unknown`。

        Args:
            mode: `pending` 服务普通恢复，`failed` 只服务显式 retry。
            delivery_ids: 可选 delivery ID 白名单；空序列表示无工作。
            excluded_message_ids: 本轮已处理、不得再次领取的消息 ID。

        Returns:
            领取成功返回冻结消息与 target，队列为空返回 `None`。
        """
        _validate_claim_mode(mode)
        filters = [TelegramSummaryMessageModel.status == mode]
        normalized_delivery_ids = _normalize_optional_ids(delivery_ids)
        if normalized_delivery_ids == ():
            return None
        if normalized_delivery_ids is not None:
            filters.append(
                TelegramSummaryMessageModel.telegram_delivery_id.in_(
                    normalized_delivery_ids
                )
            )
        excluded_ids = _normalize_ids(excluded_message_ids)
        if excluded_ids:
            filters.append(TelegramSummaryMessageModel.id.not_in(excluded_ids))
        candidate = (
            select(TelegramSummaryMessageModel.id)
            .where(*filters)
            .order_by(
                TelegramSummaryMessageModel.created_at,
                TelegramSummaryMessageModel.id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        model = self._session.scalars(
            update(TelegramSummaryMessageModel)
            .where(TelegramSummaryMessageModel.id == candidate)
            .values(
                status="running",
                attempt_count=TelegramSummaryMessageModel.attempt_count + 1,
                started_at=func.now(),
                failure_reason=None,
                failure_log=None,
                updated_at=func.now(),
            )
            .returning(TelegramSummaryMessageModel)
        ).one_or_none()
        if model is None:
            return None
        delivery = self._session.get(TelegramDeliveryModel, model.telegram_delivery_id)
        if delivery is None:
            raise RecordNotFoundError("文本消息关联的 delivery 不存在")
        return TelegramSummaryClaim(
            message=_map_summary_message(model), delivery=_map_delivery(delivery)
        )

    def claim_next_document(
        self,
        *,
        mode: Literal["pending", "failed"] = "pending",
        delivery_ids: Sequence[int] | None = None,
        excluded_message_ids: Sequence[int] = (),
    ) -> TelegramDocumentClaim | None:
        """在同一 delivery 文本已 sent 后原子领取 document。

        Args:
            mode: `pending` 服务普通恢复，`failed` 只服务显式 retry。
            delivery_ids: 可选 delivery ID 白名单；空序列表示无工作。
            excluded_message_ids: 本轮已处理、不得再次领取的消息 ID。

        Returns:
            领取成功返回冻结消息与 target，队列为空返回 `None`。
        """
        _validate_claim_mode(mode)
        summary_sent = exists(
            select(TelegramSummaryMessageModel.id).where(
                TelegramSummaryMessageModel.telegram_delivery_id
                == TelegramDocumentMessageModel.telegram_delivery_id,
                TelegramSummaryMessageModel.status == "sent",
            )
        )
        filters = [TelegramDocumentMessageModel.status == mode, summary_sent]
        normalized_delivery_ids = _normalize_optional_ids(delivery_ids)
        if normalized_delivery_ids == ():
            return None
        if normalized_delivery_ids is not None:
            filters.append(
                TelegramDocumentMessageModel.telegram_delivery_id.in_(
                    normalized_delivery_ids
                )
            )
        excluded_ids = _normalize_ids(excluded_message_ids)
        if excluded_ids:
            filters.append(TelegramDocumentMessageModel.id.not_in(excluded_ids))
        candidate = (
            select(TelegramDocumentMessageModel.id)
            .where(*filters)
            .order_by(
                TelegramDocumentMessageModel.created_at,
                TelegramDocumentMessageModel.id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        model = self._session.scalars(
            update(TelegramDocumentMessageModel)
            .where(TelegramDocumentMessageModel.id == candidate)
            .values(
                status="running",
                attempt_count=TelegramDocumentMessageModel.attempt_count + 1,
                started_at=func.now(),
                failure_reason=None,
                failure_log=None,
                updated_at=func.now(),
            )
            .returning(TelegramDocumentMessageModel)
        ).one_or_none()
        if model is None:
            return None
        delivery = self._session.get(TelegramDeliveryModel, model.telegram_delivery_id)
        if delivery is None:
            raise RecordNotFoundError("document 消息关联的 delivery 不存在")
        return TelegramDocumentClaim(
            message=_map_document_message(model), delivery=_map_delivery(delivery)
        )

    def save_summary_sent(
        self,
        message_id: int,
        *,
        result_chat_id: int,
        telegram_message_id: int,
        result_message_thread_id: int | None = None,
        telegram_message_url: str | None = None,
    ) -> TelegramSummaryMessageRecord:
        """保存 Telegram 已确认的文本发送结果。

        Args:
            message_id: 文本消息内部 ID。
            result_chat_id: Telegram 响应中的实际 chat ID。
            telegram_message_id: chat 内消息 ID。
            result_message_thread_id: Telegram 响应中的可选 topic ID。
            telegram_message_url: Telegram `Message.link` 可空快照。

        Returns:
            sent 冻结文本消息。
        """
        model = self._save_sent(
            TelegramSummaryMessageModel,
            message_id,
            result_chat_id=result_chat_id,
            telegram_message_id=telegram_message_id,
            result_message_thread_id=result_message_thread_id,
            telegram_message_url=telegram_message_url,
        )
        return _map_summary_message(model)

    def save_document_sent(
        self,
        message_id: int,
        *,
        result_chat_id: int,
        telegram_message_id: int,
        result_message_thread_id: int | None = None,
        telegram_message_url: str | None = None,
    ) -> TelegramDocumentMessageRecord:
        """保存 Telegram 已确认的 document 发送结果。

        Args:
            message_id: document 消息内部 ID。
            result_chat_id: Telegram 响应中的实际 chat ID。
            telegram_message_id: chat 内消息 ID。
            result_message_thread_id: Telegram 响应中的可选 topic ID。
            telegram_message_url: Telegram `Message.link` 可空快照。

        Returns:
            sent 冻结 document 消息。
        """
        model = self._save_sent(
            TelegramDocumentMessageModel,
            message_id,
            result_chat_id=result_chat_id,
            telegram_message_id=telegram_message_id,
            result_message_thread_id=result_message_thread_id,
            telegram_message_url=telegram_message_url,
        )
        return _map_document_message(model)

    def save_summary_failed(
        self, message_id: int, *, reason: str, failure_log: str
    ) -> TelegramSummaryMessageRecord:
        """保存一条确定失败的文本消息。

        Args:
            message_id: 文本消息内部 ID。
            reason: 简短确定失败原因。
            failure_log: 受控诊断文本。

        Returns:
            failed 冻结文本消息。
        """
        return _map_summary_message(
            self._save_unsent_terminal(
                TelegramSummaryMessageModel,
                message_id,
                status="failed",
                reason=reason,
                failure_log=failure_log,
            )
        )

    def save_document_failed(
        self, message_id: int, *, reason: str, failure_log: str
    ) -> TelegramDocumentMessageRecord:
        """保存一条确定失败的 document 消息。

        Args:
            message_id: document 消息内部 ID。
            reason: 简短确定失败原因。
            failure_log: 受控诊断文本。

        Returns:
            failed 冻结 document 消息。
        """
        return _map_document_message(
            self._save_unsent_terminal(
                TelegramDocumentMessageModel,
                message_id,
                status="failed",
                reason=reason,
                failure_log=failure_log,
            )
        )

    def save_summary_unknown(
        self, message_id: int, *, reason: str, failure_log: str
    ) -> TelegramSummaryMessageRecord:
        """把可能已发送的文本结果保存为不可自动重试的 unknown。

        Args:
            message_id: 文本消息内部 ID。
            reason: 简短结果不确定原因。
            failure_log: 受控诊断文本。

        Returns:
            unknown 冻结文本消息。
        """
        return _map_summary_message(
            self._save_unsent_terminal(
                TelegramSummaryMessageModel,
                message_id,
                status="unknown",
                reason=reason,
                failure_log=failure_log,
            )
        )

    def save_document_unknown(
        self, message_id: int, *, reason: str, failure_log: str
    ) -> TelegramDocumentMessageRecord:
        """把可能已发送的 document 结果保存为不可自动重试的 unknown。

        Args:
            message_id: document 消息内部 ID。
            reason: 简短结果不确定原因。
            failure_log: 受控诊断文本。

        Returns:
            unknown 冻结 document 消息。
        """
        return _map_document_message(
            self._save_unsent_terminal(
                TelegramDocumentMessageModel,
                message_id,
                status="unknown",
                reason=reason,
                failure_log=failure_log,
            )
        )

    def recover_stale_running(
        self, *, started_before: datetime
    ) -> StaleTelegramRecovery:
        """把两类 stale running 一律转为 unknown，阻止自动重发。

        Args:
            started_before: 早于该时刻仍 running 的消息视为结果不确定。

        Returns:
            两类消息各自恢复数量。
        """
        counts: list[int] = []
        for model in (TelegramSummaryMessageModel, TelegramDocumentMessageModel):
            result = self._session.execute(
                update(model)
                .where(model.status == "running", model.started_at < started_before)
                .values(
                    status="unknown",
                    failure_reason="stale running Telegram 结果不可确认",
                    failure_log="stale running 消息可能已经发送，禁止自动重试",
                    updated_at=func.now(),
                )
            )
            counts.append(result.rowcount)
        return StaleTelegramRecovery(
            summary_messages=counts[0], document_messages=counts[1]
        )

    def _save_sent(
        self,
        model_type,
        message_id: int,
        *,
        result_chat_id: int,
        telegram_message_id: int,
        result_message_thread_id: int | None,
        telegram_message_url: str | None,
    ):
        model = self._session.scalars(
            update(model_type)
            .where(model_type.id == message_id, model_type.status == "running")
            .values(
                status="sent",
                result_chat_id=result_chat_id,
                result_message_thread_id=result_message_thread_id,
                telegram_message_id=telegram_message_id,
                telegram_message_url=telegram_message_url,
                sent_at=func.now(),
                failure_reason=None,
                failure_log=None,
                updated_at=func.now(),
            )
            .returning(model_type)
        ).one_or_none()
        if model is None:
            existing = self._session.get(model_type, message_id)
            if existing is not None and all(
                (
                    existing.status == "sent",
                    existing.result_chat_id == result_chat_id,
                    existing.result_message_thread_id == result_message_thread_id,
                    existing.telegram_message_id == telegram_message_id,
                    existing.telegram_message_url == telegram_message_url,
                )
            ):
                # COMMIT 响应丢失时调用方会只重试数据库终态保存；接受完全相同结果
                # 可以确认已经落库，同时严禁用不同外部 ID 覆盖历史发送事实。
                return existing
            raise InvalidStateTransitionError("只有 running Telegram 消息可以保存成功")
        return model

    def _save_unsent_terminal(
        self,
        model_type,
        message_id: int,
        *,
        status: str,
        reason: str,
        failure_log: str,
    ):
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("reason 不能为空")
        model = self._session.scalars(
            update(model_type)
            .where(model_type.id == message_id, model_type.status == "running")
            .values(
                status=status,
                failure_reason=normalized_reason,
                failure_log=failure_log,
                updated_at=func.now(),
            )
            .returning(model_type)
        ).one_or_none()
        if model is None:
            existing = self._session.get(model_type, message_id)
            if (
                existing is not None
                and existing.status == status
                and existing.failure_reason == normalized_reason
                and existing.failure_log == failure_log
            ):
                return existing
            raise InvalidStateTransitionError(
                "只有 running Telegram 消息可以保存失败或 unknown"
            )
        return model


def _normalize_optional_ids(values: Sequence[int] | None) -> tuple[int, ...] | None:
    if values is None:
        return None
    return _normalize_ids(values)


def _validate_claim_mode(mode: str) -> None:
    if mode not in {"pending", "failed"}:
        raise ValueError("Telegram claim mode 只能是 pending 或 failed")


def _normalize_ids(values: Sequence[int]) -> tuple[int, ...]:
    normalized = tuple(dict.fromkeys(values))
    if any(value <= 0 for value in normalized):
        raise ValueError("内部 ID 必须全部大于 0")
    return normalized
