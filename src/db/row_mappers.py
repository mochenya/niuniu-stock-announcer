from __future__ import annotations

from pathlib import Path
from typing import Any

from cninfo_announcement.models import BusinessAnnouncement

from db.records import DeliveryCandidateRecord, SummaryCandidateRecord


def build_summary_candidate_record(row: dict[str, Any]) -> SummaryCandidateRecord:
    """把摘要候选 SQL 行还原为摘要阶段专用记录。"""
    return SummaryCandidateRecord(
        **_build_common_fields(row),
        pdf_local_path=_optional_path(row.get("pdf_local_path")),
        summary_failure_count=row.get("summary_failure_count", 0) or 0,
    )


def build_delivery_candidate_record(row: dict[str, Any]) -> DeliveryCandidateRecord:
    """把投递候选 SQL 行还原为投递阶段专用记录。"""
    pdf_local_path = row.get("pdf_local_path")
    if pdf_local_path is None:
        raise ValueError("delivery candidate requires pdf_local_path")
    return DeliveryCandidateRecord(
        **_build_common_fields(row),
        summary_status=row["summary_status"],
        pdf_local_path=Path(pdf_local_path),
        summary_text=row.get("summary_text"),
        summary_tags=row.get("summary_tags") or [],
        delivery_id=int(row["delivery_id"]),
        target_key=row["target_key"],
        text_message_id=row.get("text_message_id"),
        pdf_message_id=row.get("pdf_message_id"),
    )


def _build_common_fields(row: dict[str, Any]) -> dict[str, Any]:
    """构造两个候选记录共享的公告上下文。"""
    return {
        "source": row["source"],
        "announcement_id": row["announcement_id"],
        "announcement": _build_announcement(row),
        "market": row["market"],
        "stock_code": row["stock_code"],
        "stock_key": row["stock_key"],
        "company_name": row["company_name"],
        "search_keyword": row.get("search_keyword"),
    }


def _build_announcement(row: dict[str, Any]) -> BusinessAnnouncement:
    """把数据库公告字段还原成上游 SDK 的统一公告对象。"""
    return BusinessAnnouncement(
        source=row["source"],
        sec_code=row["sec_code"],
        sec_name=row["sec_name"],
        org_id=row["org_id"],
        announcement_id=row["announcement_id"],
        announcement_title=row["announcement_title"],
        announcement_time=row["announcement_time_ms"],
        adjunct_url=row["adjunct_url"],
        page_column=row["page_column"],
    )


def _optional_path(value: object) -> Path | None:
    if value is None:
        return None
    return Path(value)
