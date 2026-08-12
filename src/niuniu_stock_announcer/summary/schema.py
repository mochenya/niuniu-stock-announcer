"""摘要 Agent 与持久化之间共享的冻结业务 Schema。"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _require_nonblank_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("文本不能为空")
    return normalized


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChinaAgentPayload(_FrozenSchema):
    """描述 China Agent 必须从 LLM JSON 中解析出的结构。"""

    summary: str
    tags: tuple[str, ...]

    @field_validator("summary")
    @classmethod
    def _normalize_summary(cls, value: str) -> str:
        return _require_nonblank_text(value)

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value if item.strip())
        if len(normalized) < 3 or len(normalized) > 6:
            raise ValueError("tags 必须包含 3 到 6 个非空标签")
        return normalized


class ChinaSummaryResult(_FrozenSchema):
    """保存 China Agent 输出的版本化权威结果。"""

    schema_version: Literal["china-announcement-summary.v1"] = (
        "china-announcement-summary.v1"
    )
    summary_text: str
    summary_tags: tuple[str, ...]

    @field_validator("summary_text")
    @classmethod
    def _require_summary_text(cls, value: str) -> str:
        return _require_nonblank_text(value)

    @field_validator("summary_tags")
    @classmethod
    def _require_summary_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value if item.strip())
        if len(normalized) < 3 or len(normalized) > 6:
            raise ValueError("summary_tags 必须包含 3 到 6 个非空标签")
        return normalized


class SummaryCompletion(_FrozenSchema):
    """描述一次成功摘要的审计字段和权威结果，不保存完整 LLM response。"""

    agent_key: str
    agent_version: str
    prompt_version: str
    model_provider: str | None = None
    model_name: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    result: ChinaSummaryResult

    @field_validator("agent_key", "agent_version", "prompt_version", "model_name")
    @classmethod
    def _normalize_required_text(cls, value: str) -> str:
        return _require_nonblank_text(value)


class SummaryAgentInput(_FrozenSchema):
    """描述脱离数据库 Session 后交给摘要 Agent 的最小输入。"""

    announcement_id: str
    company_name: str
    announcement_title: str
    markdown: str

    @field_validator(
        "announcement_id", "company_name", "announcement_title", "markdown"
    )
    @classmethod
    def _normalize_required_text(cls, value: str) -> str:
        return _require_nonblank_text(value)


@runtime_checkable
class SummaryAgent(Protocol):
    """定义市场摘要策略的 typed 输入/输出协议。"""

    def summarize(self, request: SummaryAgentInput) -> SummaryCompletion:
        """根据公告 Markdown 返回验证后的摘要与审计字段。

        Args:
            request: 脱离数据库 Session 的公告摘要输入。

        Returns:
            版本化摘要结果与最小审计字段。
        """
