"""China Agent JSON 解析、字段校验和一次修复判定。"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from niuniu_stock_announcer.summary.errors import SummaryError
from niuniu_stock_announcer.summary.schema import ChinaAgentPayload

REPAIRABLE_ERROR_CODES = frozenset(
    {"PARSE_EMPTY", "PARSE_JSON", "PARSE_OBJECT", "PARSE_SCHEMA"}
)


def parse_china_agent_payload(raw_content: str) -> ChinaAgentPayload:
    """把 LLM 输出解析为严格的 China 摘要 JSON Schema。"""
    normalized = normalize_summary_payload_text(raw_content)
    if not normalized:
        raise SummaryError("PARSE_EMPTY", "LLM 没有返回 JSON 内容")
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise SummaryError("PARSE_JSON", _format_parse_error(exc)) from exc
    if not isinstance(payload, dict):
        raise SummaryError("PARSE_OBJECT", "LLM 输出不是 JSON 对象")
    try:
        return ChinaAgentPayload.model_validate(payload)
    except ValidationError as exc:
        raise SummaryError("PARSE_SCHEMA", _format_parse_error(exc)) from exc


def normalize_summary_payload_text(raw_content: str) -> str:
    """清理 BOM 和偶发代码块包裹，保留真正的 JSON 文本。"""
    normalized = raw_content.strip().lstrip("\ufeff").strip()
    if not normalized:
        return ""
    return unwrap_code_fence(normalized).strip().lstrip("\ufeff").strip()


def unwrap_code_fence(raw_content: str) -> str:
    """移除 LLM 偶发添加的 JSON markdown fence。"""
    match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        raw_content,
        re.IGNORECASE | re.DOTALL,
    )
    return raw_content if match is None else match.group(1)


def should_attempt_repair(exc: SummaryError) -> bool:
    """判断异常是否属于最多修复一次的格式/Schema 错误。"""
    return exc.code in REPAIRABLE_ERROR_CODES


def format_repair_error(raw_content: str, exc: SummaryError | None) -> str:
    """生成不回显完整错误输出的修复提示诊断。"""
    if exc is None:
        return "PARSE_UNKNOWN: unable to parse previous output"
    suffix = ". 上一次输出为空。" if not raw_content.strip() else ""
    return f"{exc.code}: {exc.message}{suffix}"


def _format_parse_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        details = []
        for error in exc.errors(include_input=False, include_url=False):
            location = ".".join(str(part) for part in error["loc"])
            details.append(f"{location}: {error['msg']}")
        return "; ".join(details)
    message = str(exc).strip()
    return message or exc.__class__.__name__
