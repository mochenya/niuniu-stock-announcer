from __future__ import annotations

from pathlib import Path

import pymupdf4llm

from domain.config_models import RuntimeConfig
from domain.summary_models import (
    MarkdownSummaryRequest,
    PdfSummaryRequest,
    SummaryRunResult,
)
from summary.client import SummaryLLMClient


def summarize_pdf(
    request: PdfSummaryRequest,
    *,
    config: RuntimeConfig | None = None,
    llm_client: SummaryLLMClient | None = None,
) -> SummaryRunResult:
    """从 PDF 提取 Markdown 后调用 LLM 生成结构化摘要。

    工作流会优先传入复用的 llm_client；单独调用时才临时创建客户端。
    """
    markdown = extract_pdf_markdown(request.pdf_path)
    markdown_request = MarkdownSummaryRequest(
        announcement_id=request.announcement_id,
        company_name=request.company_name,
        announcement_title=request.announcement_title,
        markdown=markdown,
    )
    if llm_client is not None:
        return llm_client.summarize_markdown(markdown_request)
    with SummaryLLMClient(config=config) as summary_client:
        return summary_client.summarize_markdown(markdown_request)


def extract_pdf_markdown(pdf_path: str | Path) -> str:
    """把 PDF 转为 Markdown，供摘要 prompt 使用。"""
    return pymupdf4llm.to_markdown(str(pdf_path), footer=False)
