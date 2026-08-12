"""不可变投递 payload 渲染与事务内物化。"""

from __future__ import annotations

from pathlib import PurePosixPath

from niuniu_stock_announcer.db.schema import (
    ChinaMatchRecord,
    ChinaSummaryRenderContext,
    TelegramDeliveryRecord,
    TelegramDocumentMessageWrite,
    TelegramSummaryMessageWrite,
)
from niuniu_stock_announcer.db.unit_of_work import UnitOfWork
from niuniu_stock_announcer.delivery.schema import (
    ChinaDeliveryRenderInput,
    DeliveryDocumentPayload,
    DeliveryMaterialization,
    DeliverySummaryPayload,
)
from niuniu_stock_announcer.im.telegram.format import (
    format_telegram_document_caption,
    format_telegram_summary_text,
)


class DeliveryService:
    """只从冻结数据库快照渲染 Telegram 文本和原文 payload。"""

    def render(
        self,
        context: ChinaSummaryRenderContext,
        delivery: TelegramDeliveryRecord,
    ) -> DeliveryMaterialization:
        """为一个终态 China 摘要渲染一次性冻结的投递 payload。

        Args:
            context: 已脱离 ORM Session 的公告、摘要与 selected match 快照。
            delivery: 已冻结 target 和原文开关的逻辑投递快照。

        Returns:
            可由 Repository 插入且之后不再重渲染的文本/document payload。

        Raises:
            ValueError: owner 身份、match、摘要终态或 PDF 快照不一致。
        """
        _validate_delivery_owner(context, delivery)
        match = _select_delivery_match(context, delivery)
        render_input = _build_render_input(context, match)
        documents: tuple[DeliveryDocumentPayload, ...] = ()
        if delivery.send_original_document or context.summary.status == "skipped":
            pdf = context.announcement.pdf
            if pdf is None:
                raise ValueError("需要发送原文时公告必须已有已验证 PDF 快照")
            documents = (
                DeliveryDocumentPayload(
                    document_key="original",
                    source_url=context.announcement.source_url,
                    storage_relative_path=pdf.storage_relative_path,
                    document_filename=PurePosixPath(pdf.storage_relative_path).name,
                    document_mime_type="application/pdf",
                    document_size_bytes=pdf.size_bytes,
                    document_sha256=pdf.sha256,
                    document_caption=format_telegram_document_caption(render_input),
                ),
            )
        return DeliveryMaterialization(
            summary=DeliverySummaryPayload(
                text_content=format_telegram_summary_text(render_input)
            ),
            documents=documents,
        )


class ChinaDeliveryMaterializer:
    """在调用方已经锁定 summary 的事务内插入缺失 child payload。"""

    def __init__(self, service: DeliveryService | None = None) -> None:
        """绑定纯 Delivery Service。

        Args:
            service: 可注入的纯渲染服务；省略时使用默认实现。
        """
        self._service = service or DeliveryService()

    def __call__(self, uow: UnitOfWork, summary_id: int, delivery_id: int) -> None:
        """读取冻结上下文并幂等插入一个 delivery 的 child messages。

        Args:
            uow: 已进入且由 Stage 管理提交的短事务。
            summary_id: 已由调用方按统一顺序锁定的 China 摘要 ID。
            delivery_id: 需要物化的 Telegram delivery ID。

        Raises:
            ValueError: 上下文不满足终态物化约束。
        """
        context = uow.china_summaries.get_render_context(summary_id)
        delivery = uow.telegram.get_delivery(delivery_id)
        materialization = self._service.render(context, delivery)
        uow.telegram.insert_summary_message(
            TelegramSummaryMessageWrite(
                telegram_delivery_id=delivery_id,
                text_content=materialization.summary.text_content,
            )
        )
        for document in materialization.documents:
            uow.telegram.insert_document_message(
                TelegramDocumentMessageWrite(
                    telegram_delivery_id=delivery_id,
                    **document.model_dump(mode="python"),
                )
            )


def _validate_delivery_owner(
    context: ChinaSummaryRenderContext,
    delivery: TelegramDeliveryRecord,
) -> None:
    if delivery.producer_key != "china_summary":
        raise ValueError("Delivery Service 只接受 china_summary owner")
    if delivery.business_key != str(context.summary.id):
        raise ValueError("delivery business_key 与 summary ID 不一致")
    if context.summary.china_announcement_id != context.announcement.id:
        raise ValueError("summary 与 announcement 身份不一致")
    if delivery.market_scope != context.announcement.market_scope:
        raise ValueError("delivery 与 announcement market scope 不一致")
    if context.summary.status not in {"completed", "skipped"}:
        raise ValueError("只有终态摘要可以物化投递 payload")


def _select_delivery_match(
    context: ChinaSummaryRenderContext,
    delivery: TelegramDeliveryRecord,
) -> ChinaMatchRecord:
    matches = tuple(
        match
        for match in context.selected_matches
        if match.plan_key == delivery.plan_key
        and match.market_scope == delivery.market_scope
    )
    if len(matches) != 1:
        raise ValueError("delivery 必须恰好对应一条 selected match 快照")
    return matches[0]


def _build_render_input(
    context: ChinaSummaryRenderContext,
    match: ChinaMatchRecord,
) -> ChinaDeliveryRenderInput:
    announcement = context.announcement
    exchange = match.query_exchange
    stock_code = match.query_stock_code
    company_name = None
    if stock_code is not None:
        for item_exchange, item_code, item_name in zip(
            announcement.exchanges,
            announcement.stock_codes,
            announcement.stock_names,
            strict=True,
        ):
            if item_exchange == exchange and item_code == stock_code:
                company_name = item_name
                break
    elif announcement.stock_codes:
        exchange = announcement.exchanges[0]
        stock_code = announcement.stock_codes[0]
        company_name = announcement.stock_names[0]
    if company_name is None:
        company_name = next(
            (name for name in announcement.stock_names if name is not None),
            None,
        )

    result = context.summary.result
    if context.summary.status == "completed":
        if result is None:
            raise ValueError("completed 摘要缺少权威 result")
        summary_text = result.summary_text
        summary_tags = result.summary_tags
    else:
        if result is not None:
            raise ValueError("skipped 摘要不能保留成功 result")
        summary_text = None
        summary_tags = ()
    return ChinaDeliveryRenderInput(
        provider_key=announcement.provider_key,
        provider_announcement_id=announcement.provider_announcement_id,
        title=announcement.title,
        published_at=announcement.published_at,
        company_name=company_name,
        exchange=exchange,
        stock_code=stock_code,
        discovery_type=match.discovery_type,
        matched_search_keywords=match.matched_search_keywords,
        summary_status=context.summary.status,
        summary_text=summary_text,
        summary_tags=summary_tags,
    )
