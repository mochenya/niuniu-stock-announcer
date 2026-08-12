"""China ORM Model 到冻结 Schema 的集中转换。"""

from niuniu_stock_announcer.db.model.china import (
    ChinaAnnouncementModel,
    ChinaSummaryModel,
)
from niuniu_stock_announcer.db.schema import (
    ChinaAnnouncementRecord,
    ChinaSummaryRecord,
    ChinaSummaryResult,
    PdfSnapshot,
)


def map_china_announcement(model: ChinaAnnouncementModel) -> ChinaAnnouncementRecord:
    pdf = None
    if model.pdf_storage_relative_path is not None:
        pdf = PdfSnapshot(
            storage_relative_path=model.pdf_storage_relative_path,
            size_bytes=model.pdf_size_bytes,
            sha256=model.pdf_sha256,
        )
    return ChinaAnnouncementRecord(
        id=model.id,
        provider_key=model.provider_key,
        provider_announcement_id=model.provider_announcement_id,
        market_scope=model.market_scope,
        exchanges=tuple(model.exchanges),
        stock_codes=tuple(model.stock_codes),
        stock_names=tuple(model.stock_names),
        title=model.title,
        published_at=model.published_at,
        source_url=model.source_url,
        pdf=pdf,
        first_seen_at=model.first_seen_at,
        last_seen_at=model.last_seen_at,
    )


def map_china_summary(model: ChinaSummaryModel) -> ChinaSummaryRecord:
    result = None
    if model.summary_result is not None:
        result = ChinaSummaryResult.model_validate(model.summary_result)
    return ChinaSummaryRecord(
        id=model.id,
        china_announcement_id=model.china_announcement_id,
        status=model.status,
        failure_count=model.failure_count,
        agent_key=model.agent_key,
        agent_version=model.agent_version,
        prompt_version=model.prompt_version,
        model_provider=model.model_provider,
        model_name=model.model_name,
        input_tokens=model.input_tokens,
        output_tokens=model.output_tokens,
        result=result,
        failure_reason=model.failure_reason,
        failure_log=model.failure_log,
        started_at=model.started_at,
        finished_at=model.finished_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )
