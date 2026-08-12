"""PDF 文档提取与摘要 Agent 调用服务。"""

from __future__ import annotations

from typing import Protocol
from pathlib import Path

import pymupdf4llm

from niuniu_stock_announcer.announcements.schema import (
    ChinaAnnouncement,
    StoredAnnouncementDocument,
)
from niuniu_stock_announcer.summary.errors import SummaryError
from niuniu_stock_announcer.summary.schema import (
    SummaryAgent,
    SummaryAgentInput,
    SummaryCompletion,
)


class AnnouncementDocumentLoader(Protocol):
    """定义 Summary Service 所需的最小公告文档能力。"""

    def ensure_pdf(self, announcement: ChinaAnnouncement) -> StoredAnnouncementDocument:
        """返回经过路径与结构验证的本地 PDF。

        Args:
            announcement: 脱离 ORM 的 China 公告事实。

        Returns:
            可安全交给 Markdown 提取器的文档快照。
        """


class SummaryService:
    """组合 PDF 生命周期输入、Markdown 提取和已注入的摘要 Agent。"""

    def __init__(
        self,
        document_service: AnnouncementDocumentLoader,
        agent: SummaryAgent,
    ) -> None:
        """绑定文档服务和市场 Agent；不创建事务或读取环境。

        Args:
            document_service: 负责下载、复用和验证公告 PDF 的服务。
            agent: 已注入的 typed 摘要 Agent。
        """
        self._document_service = document_service
        self._agent = agent

    def ensure_pdf(self, announcement: ChinaAnnouncement) -> StoredAnnouncementDocument:
        """确保公告拥有可供摘要复用的已验证 PDF。

        Args:
            announcement: 脱离 ORM 的 China 公告事实。

        Returns:
            已验证且位于 storage root 内的 PDF 快照。
        """
        return self._document_service.ensure_pdf(announcement)

    def summarize_document(
        self,
        announcement: ChinaAnnouncement,
        document: StoredAnnouncementDocument,
    ) -> SummaryCompletion:
        """提取 PDF Markdown、调用 Agent 并再次校验结果。

        Args:
            announcement: 脱离 ORM 的公告事实。
            document: 已通过文档服务验证的 PDF。

        Returns:
            只含版本化结果和审计字段的摘要完成记录。

        Raises:
            ValueError: 文档身份与公告不一致。
        """
        if (
            document.provider_key != announcement.provider_key
            or document.provider_announcement_id
            != announcement.provider_announcement_id
            or document.source_url != announcement.source_url
        ):
            raise ValueError("摘要文档身份与公告不一致")
        markdown = extract_pdf_markdown(document.local_path)
        if not markdown.strip():
            raise SummaryError("EXTRACT_EMPTY", "PDF 未提取到可摘要文本")
        request = SummaryAgentInput(
            announcement_id=announcement.provider_announcement_id,
            company_name=_company_name(announcement),
            announcement_title=announcement.title,
            markdown=markdown,
        )
        return SummaryCompletion.model_validate(self._agent.summarize(request))


def extract_pdf_markdown(pdf_path: str | Path) -> str:
    """把 PDF 转为 Markdown，供摘要 Agent 使用。

    Args:
        pdf_path: 已验证的本地 PDF 路径。

    Returns:
        PyMuPDF4LLM 提取的 Markdown 文本。
    """
    return pymupdf4llm.to_markdown(str(pdf_path), footer=False)


def _company_name(announcement: ChinaAnnouncement) -> str:
    for security in announcement.securities:
        if security.stock_name:
            return security.stock_name
    for security in announcement.securities:
        if security.stock_code:
            return security.stock_code
    return announcement.provider_announcement_id
