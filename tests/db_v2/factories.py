"""v2 PostgreSQL 集成测试的明确业务样本工厂。"""

from datetime import UTC, datetime

from niuniu_stock_announcer.db.schema import (
    ChinaAnnouncementWrite,
    ChinaMatchWrite,
    CninfoAnnouncementWrite,
    SseAnnouncementWrite,
    SzseAnnouncementWrite,
    SummaryCompletion,
    ChinaSummaryResult,
    TelegramDeliveryWrite,
    TelegramDocumentMessageWrite,
    TelegramSummaryMessageWrite,
    TitleFilterDecision,
    TitleFilterEvidence,
)


def announcement(
    identity: str = "announcement-1",
    *,
    provider_key: str = "cninfo",
    market_scope: str = "a_share",
) -> ChinaAnnouncementWrite:
    exchanges = ("hk",) if market_scope == "hk" else ("sh",)
    stock_codes = ("06869",) if market_scope == "hk" else ("688090",)
    return ChinaAnnouncementWrite(
        provider_key=provider_key,
        provider_announcement_id=identity,
        market_scope=market_scope,
        exchanges=exchanges,
        stock_codes=stock_codes,
        stock_names=("长飞光纤",) if market_scope == "hk" else ("瑞松科技",),
        title=f"测试公告 {identity}",
        published_at=datetime(2026, 8, 12, 9, tzinfo=UTC),
        source_url=f"https://example.invalid/{identity}.pdf",
    )


def cninfo_raw(announcement_id: int, identity: str) -> CninfoAnnouncementWrite:
    return CninfoAnnouncementWrite(
        china_announcement_id=announcement_id,
        announcement_id=identity,
        sec_code="688090",
        sec_name="瑞松科技",
        org_id="gssh0600688",
        announcement_title="测试公告",
        announcement_time_ms=1786496400000,
        adjunct_url=f"finalpage/2026-08-12/{identity}.PDF",
        adjunct_size=1024,
        adjunct_type="PDF",
        column_id="250401||251302",
        page_column="SHKCB",
        announcement_type="01010501||010113||012399",
    )


def sse_raw(announcement_id: int, identity: str) -> SseAnnouncementWrite:
    return SseAnnouncementWrite(
        china_announcement_id=announcement_id,
        provider_announcement_id=identity,
        security_code="688090",
        security_name="瑞松科技",
        org_bulletin_id="202608120001",
        title="测试公告",
        sse_date="2026-08-12",
        url=f"/disclosure/listedinfo/announcement/c/new/{identity}.pdf",
        bulletin_type_desc="临时公告",
        is_holder_disclose="0",
    )


def szse_raw(announcement_id: int, identity: str) -> SzseAnnouncementWrite:
    return SzseAnnouncementWrite(
        china_announcement_id=announcement_id,
        provider_announcement_id=identity,
        ann_id=identity,
        source_record_id=f"record-{identity}",
        sec_codes=("000510",),
        sec_names=("新金路",),
        title="测试公告",
        publish_time="2026-08-12 08:00:00",
        attach_path=f"/disc/disk03/finalpage/{identity}.PDF",
        attach_format="PDF",
        attach_size=2048,
        bond_type="",
        big_industry_code="C",
        big_category_id="0101",
        small_category_id="010101",
        channel_code="listedNotice_disc",
    )


def selected_match(
    announcement_id: int,
    *,
    plan_key: str = "selected-plan",
    filtered: bool = False,
) -> ChinaMatchWrite:
    matched = ("减持",) if filtered else ()
    decision = TitleFilterDecision(
        outcome="filtered" if filtered else "selected",
        reason_code="excluded_keyword" if filtered else "passed",
        evidence=TitleFilterEvidence(
            evaluated_title="关于股东减持计划的公告"
            if filtered
            else "关于股份回购的公告",
            configured_keywords=("减持",),
            matched_keywords=matched,
        ),
    )
    return ChinaMatchWrite(
        china_announcement_id=announcement_id,
        plan_key=plan_key,
        discovery_type="selected_stocks",
        market_scope="a_share",
        query_exchange="sh",
        query_stock_code="688090",
        query_provider_key="cninfo",
        filter_status="filtered" if filtered else "selected",
        filter_decisions=(decision,),
    )


def keyword_match(
    announcement_id: int,
    *,
    plan_key: str = "keyword-plan",
    keywords: tuple[str, ...] = ("回购",),
) -> ChinaMatchWrite:
    decision = TitleFilterDecision(
        outcome="selected",
        reason_code="passed",
        evidence=TitleFilterEvidence(
            evaluated_title="关于股份回购的公告",
            configured_keywords=("减持",),
            matched_keywords=(),
        ),
    )
    return ChinaMatchWrite(
        china_announcement_id=announcement_id,
        plan_key=plan_key,
        discovery_type="market_keywords",
        market_scope="a_share",
        matched_search_keywords=keywords,
        filter_status="selected",
        filter_decisions=(decision,),
    )


def delivery(
    summary_id: int,
    *,
    plan_key: str = "selected-plan",
    target_url: str = "https://t.me/example/100",
    send_original_document: bool = True,
) -> TelegramDeliveryWrite:
    return TelegramDeliveryWrite(
        producer_key="china_summary",
        business_key=str(summary_id),
        plan_key=plan_key,
        market_scope="a_share",
        target_key="a-share-notices",
        target_url=target_url,
        target_chat_id=-1001234567890,
        target_message_thread_id=100,
        send_original_document=send_original_document,
    )


def summary_message(
    delivery_id: int, text: str = "测试摘要"
) -> TelegramSummaryMessageWrite:
    return TelegramSummaryMessageWrite(
        telegram_delivery_id=delivery_id, text_content=text
    )


def document_message(
    delivery_id: int, *, caption: str = "原公告"
) -> TelegramDocumentMessageWrite:
    return TelegramDocumentMessageWrite(
        telegram_delivery_id=delivery_id,
        document_key="original",
        source_url="https://example.invalid/announcement.pdf",
        storage_relative_path="cninfo/2026/08/announcement.pdf",
        document_filename="公告.pdf",
        document_mime_type="application/pdf",
        document_size_bytes=4096,
        document_sha256="a" * 64,
        document_caption=caption,
    )


def summary_completion(text: str = "回购事项摘要") -> SummaryCompletion:
    return SummaryCompletion(
        agent_key="china-announcement",
        agent_version="v1",
        prompt_version="china.v1",
        model_provider="openai-compatible",
        model_name="test-model",
        input_tokens=100,
        output_tokens=20,
        result=ChinaSummaryResult(summary_text=text, summary_tags=("回购",)),
    )
