from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain.common import normalize_required_text


class MarkdownSummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    announcement_id: str
    company_name: str
    announcement_title: str
    markdown: str

    @field_validator(
        "announcement_id",
        "company_name",
        "announcement_title",
        "markdown",
        mode="before",
    )
    @classmethod
    def _validate_non_empty_text(cls, value: object) -> str:
        return normalize_required_text(value, field_name="summary request field")


class PdfSummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    announcement_id: str
    pdf_path: Path
    company_name: str
    announcement_title: str

    @field_validator(
        "announcement_id", "company_name", "announcement_title", mode="before"
    )
    @classmethod
    def _validate_non_empty_text(cls, value: object) -> str:
        return normalize_required_text(value, field_name="summary request field")


class AnnouncementSummary(BaseModel):
    """大模型必须返回并落库的公告摘要结构。"""

    model_config = ConfigDict(extra="forbid")

    summary: str
    tags: list[str] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def _validate_summary(cls, value: object) -> str:
        return normalize_required_text(value, field_name="summary")

    @field_validator("tags", mode="after")
    @classmethod
    def _validate_tags(cls, value: list[str]) -> list[str]:
        """标签数量和清理逻辑需与提示词的输出协议保持一致。"""
        normalized_tags = [item.strip() for item in value if item.strip()]
        if len(normalized_tags) < 3 or len(normalized_tags) > 6:
            raise ValueError("tags must contain 3 to 6 items")
        return normalized_tags


class SummaryRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    announcement_id: str
    summary: AnnouncementSummary
    llm_model: str | None = None
    llm_response_json: dict[str, object] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
