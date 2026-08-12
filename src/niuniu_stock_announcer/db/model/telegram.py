"""共享 Telegram outbox ORM Model。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from niuniu_stock_announcer.db.model.base import Base


class TelegramDeliveryModel(Base):
    """冻结跨市场逻辑投递身份、target 和 document 意图。"""

    __tablename__ = "telegram_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "producer_key",
            "business_key",
            "plan_key",
            "market_scope",
            name="uq_telegram_deliveries_business_identity",
        ),
        Index(
            "idx_telegram_deliveries_plan_scope_created",
            "plan_key",
            "market_scope",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    producer_key: Mapped[str] = mapped_column(Text, nullable=False)
    business_key: Mapped[str] = mapped_column(Text, nullable=False)
    plan_key: Mapped[str] = mapped_column(Text, nullable=False)
    market_scope: Mapped[str] = mapped_column(Text, nullable=False)
    target_key: Mapped[str] = mapped_column(Text, nullable=False)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    target_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_message_thread_id: Mapped[int | None] = mapped_column(BigInteger)
    send_original_document: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class _TelegramMessageStateMixin:
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    result_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    result_message_thread_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_message_url: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    failure_log: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


MESSAGE_STATE_CONSTRAINTS = (
    CheckConstraint(
        "status IN ('pending', 'running', 'sent', 'failed', 'unknown')",
        name="status",
    ),
    CheckConstraint("attempt_count >= 0", name="attempt_count"),
    CheckConstraint(
        "(status = 'sent' AND result_chat_id IS NOT NULL "
        "AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL "
        "AND failure_reason IS NULL AND failure_log IS NULL) OR "
        "(status <> 'sent' AND result_chat_id IS NULL "
        "AND result_message_thread_id IS NULL "
        "AND telegram_message_id IS NULL "
        "AND telegram_message_url IS NULL AND sent_at IS NULL)",
        name="result_fields",
    ),
    CheckConstraint(
        "(status = 'pending' AND started_at IS NULL "
        "AND failure_reason IS NULL AND failure_log IS NULL) OR "
        "(status = 'running' AND started_at IS NOT NULL "
        "AND failure_reason IS NULL AND failure_log IS NULL) OR "
        "(status = 'sent' AND started_at IS NOT NULL) OR "
        "(status IN ('failed', 'unknown') AND started_at IS NOT NULL "
        "AND failure_reason IS NOT NULL)",
        name="lifecycle",
    ),
)


class TelegramSummaryMessageModel(_TelegramMessageStateMixin, Base):
    """保存一次逻辑投递的不可变文本 payload 与独立状态。"""

    __tablename__ = "telegram_summary_messages"
    __table_args__ = (
        UniqueConstraint(
            "telegram_delivery_id",
            name="uq_telegram_summary_messages_delivery",
        ),
        *(
            CheckConstraint(
                constraint.sqltext,
                name=f"ck_telegram_summary_messages_{constraint.name}",
            )
            for constraint in MESSAGE_STATE_CONSTRAINTS
        ),
        Index("idx_telegram_summary_messages_claim", "status", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    telegram_delivery_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "telegram_deliveries.id",
            ondelete="RESTRICT",
            name="fk_telegram_summary_messages_delivery",
        ),
        nullable=False,
    )
    text_content: Mapped[str] = mapped_column(Text, nullable=False)


class TelegramDocumentMessageModel(_TelegramMessageStateMixin, Base):
    """保存一次逻辑投递的不可变 document payload 与独立状态。"""

    __tablename__ = "telegram_document_messages"
    __table_args__ = (
        UniqueConstraint(
            "telegram_delivery_id",
            "document_key",
            name="uq_telegram_document_messages_delivery_document",
        ),
        CheckConstraint(
            "document_size_bytes > 0",
            name="ck_telegram_document_messages_document_size",
        ),
        CheckConstraint(
            "document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_telegram_document_messages_document_sha256",
        ),
        *(
            CheckConstraint(
                constraint.sqltext,
                name=f"ck_telegram_document_messages_{constraint.name}",
            )
            for constraint in MESSAGE_STATE_CONSTRAINTS
        ),
        Index("idx_telegram_document_messages_claim", "status", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    telegram_delivery_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "telegram_deliveries.id",
            ondelete="RESTRICT",
            name="fk_telegram_document_messages_delivery",
        ),
        nullable=False,
    )
    document_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    storage_relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    document_filename: Mapped[str] = mapped_column(Text, nullable=False)
    document_mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    document_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    document_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    document_caption: Mapped[str] = mapped_column(Text, nullable=False)
