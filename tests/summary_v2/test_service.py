"""Summary Service 的 PDF Markdown 与 typed Agent 边界测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pymupdf
import pytest

from niuniu_stock_announcer.announcements.schema import (
    AnnouncementSecurity,
    ChinaAnnouncement,
    StoredAnnouncementDocument,
)
from niuniu_stock_announcer.summary.schema import (
    ChinaSummaryResult,
    SummaryAgentInput,
    SummaryCompletion,
)
from niuniu_stock_announcer.summary.service import SummaryService, extract_pdf_markdown


class _DocumentService:
    def __init__(self, document: StoredAnnouncementDocument) -> None:
        self.document = document
        self.calls: list[ChinaAnnouncement] = []

    def ensure_pdf(self, announcement: ChinaAnnouncement) -> StoredAnnouncementDocument:
        self.calls.append(announcement)
        return self.document


class _Agent:
    def __init__(self) -> None:
        self.requests: list[SummaryAgentInput] = []

    def summarize(self, request: SummaryAgentInput) -> SummaryCompletion:
        self.requests.append(request)
        return SummaryCompletion(
            agent_key="china-announcement-summary",
            agent_version="v2",
            prompt_version="china-announcement-summary.v1",
            model_provider="openai-compatible",
            model_name="test-model",
            input_tokens=10,
            output_tokens=5,
            result=ChinaSummaryResult(
                summary_text="公司已完成回购。",
                summary_tags=("股份回购", "回购完成", "A股"),
            ),
        )


def _announcement() -> ChinaAnnouncement:
    return ChinaAnnouncement(
        provider_key="cninfo",
        provider_announcement_id="ann-1",
        market_scope="a_share",
        securities=(
            AnnouncementSecurity(
                exchange="sh", stock_code="688090", stock_name="瑞松科技"
            ),
        ),
        title="关于股份回购完成的公告",
        published_at=datetime(2026, 8, 12, 1, tzinfo=UTC),
        source_url="https://static.cninfo.com.cn/finalpage/ann-1.PDF",
    )


def _pdf(tmp_path: Path) -> StoredAnnouncementDocument:
    path = tmp_path / "ann-1.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Share repurchase completed")
        document.save(path)
    return StoredAnnouncementDocument(
        provider_key="cninfo",
        provider_announcement_id="ann-1",
        source_url="https://static.cninfo.com.cn/finalpage/ann-1.PDF",
        storage_relative_path="cninfo/2026/08/ann-1.pdf",
        local_path=path,
        size_bytes=path.stat().st_size,
        sha256="a" * 64,
        page_count=1,
    )


def test_extract_pdf_markdown_and_service_pass_company_context_to_agent(
    tmp_path: Path,
) -> None:
    announcement = _announcement()
    document = _pdf(tmp_path)
    document_service = _DocumentService(document)
    agent = _Agent()
    service = SummaryService(document_service, agent)

    ensured = service.ensure_pdf(announcement)
    completion = service.summarize_document(announcement, ensured)

    assert "Share repurchase completed" in extract_pdf_markdown(document.local_path)
    assert completion.result.summary_text == "公司已完成回购。"
    assert document_service.calls == [announcement]
    assert len(agent.requests) == 1
    assert agent.requests[0].company_name == "瑞松科技"
    assert "Share repurchase completed" in agent.requests[0].markdown


def test_summary_service_rejects_document_from_another_announcement(
    tmp_path: Path,
) -> None:
    announcement = _announcement()
    document = _pdf(tmp_path).model_copy(
        update={"provider_announcement_id": "other-announcement"}
    )
    agent = _Agent()
    service = SummaryService(_DocumentService(document), agent)

    with pytest.raises(ValueError, match="身份与公告不一致"):
        service.summarize_document(announcement, document)

    assert agent.requests == []


def test_summary_service_rejects_document_from_another_source_url(
    tmp_path: Path,
) -> None:
    announcement = _announcement()
    document = _pdf(tmp_path).model_copy(
        update={"source_url": "https://static.cninfo.com.cn/finalpage/other.PDF"}
    )
    agent = _Agent()
    service = SummaryService(_DocumentService(document), agent)

    with pytest.raises(ValueError, match="身份与公告不一致"):
        service.summarize_document(announcement, document)

    assert agent.requests == []
