"""过滤策略输出的版本化业务证据。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

FilterStatus = Literal["selected", "filtered"]


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TitleFilterEvidence(_FrozenSchema):
    """保存标题排除规则实际评估的输入和命中项。"""

    evaluated_title: str
    configured_keywords: tuple[str, ...]
    matched_keywords: tuple[str, ...]


class TitleFilterDecision(_FrozenSchema):
    """保存首版标题排除规则的一次版本化决定。"""

    filter_type: Literal["title_exclusion"] = "title_exclusion"
    schema_version: Literal["v1"] = "v1"
    outcome: FilterStatus
    reason_code: Literal["passed", "excluded_keyword"]
    evidence: TitleFilterEvidence

    @model_validator(mode="after")
    def _validate_projection(self) -> TitleFilterDecision:
        filtered = bool(self.evidence.matched_keywords)
        if filtered != (self.outcome == "filtered"):
            raise ValueError("标题命中证据与 outcome 不一致")
        expected_reason = "excluded_keyword" if filtered else "passed"
        if self.reason_code != expected_reason:
            raise ValueError("标题命中证据与 reason_code 不一致")
        return self
