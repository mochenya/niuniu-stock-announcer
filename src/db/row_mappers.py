from __future__ import annotations

from pathlib import Path
from typing import Any

from cninfo_announcement.models import BusinessAnnouncement

from domain.workflow_models import WorkflowCandidate


def build_workflow_candidate(row: dict[str, Any]) -> WorkflowCandidate:
    """把候选 SQL 行还原为后续阶段统一使用的 WorkflowCandidate。

    SUMMARY_CANDIDATE_SQL 和 DELIVERY_CANDIDATE_SQL 的列名必须与这里保持一致。
    """
    announcement = BusinessAnnouncement(
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
    return WorkflowCandidate(
        source=row["source"],
        announcement_id=row["announcement_id"],
        announcement=announcement,
        market=row["market"],
        stock_code=row["stock_code"],
        stock_key=row["stock_key"],
        company_name=row["company_name"],
        primary_hit_id=row["primary_hit_id"],
        search_keyword=row.get("search_keyword"),
        summary_status=row["summary_status"],
        pdf_local_path=None
        if row["pdf_local_path"] is None
        else Path(row["pdf_local_path"]),
        summary_json=row["summary_json"],
        summary_text=row["summary_text"],
        summary_tags=row["summary_tags"],
        delivery_id=row.get("delivery_id"),
        delivery_status=row.get("delivery_status"),
        target_key=row.get("target_key"),
        target_chat_id=row.get("target_chat_id"),
        target_message_thread_id=row.get("target_message_thread_id"),
        text_message_id=row.get("text_message_id"),
        pdf_message_id=row.get("pdf_message_id"),
    )
