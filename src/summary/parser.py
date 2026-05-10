from __future__ import annotations

import json
import re

from pydantic import ValidationError

from domain.summary_models import AnnouncementSummary
from summary.errors import SummaryError

REPAIRABLE_ERROR_CODES = {
    "PARSE_EMPTY",
    "PARSE_JSON",
    "PARSE_OBJECT",
    "PARSE_SCHEMA",
}


def parse_announcement_summary(raw_content: str) -> AnnouncementSummary:
    """把 LLM 原始输出解析并校验成公告摘要模型。"""
    normalized_content = normalize_summary_payload_text(raw_content)
    if not normalized_content:
        raise SummaryError("PARSE_EMPTY", "LLM did not return JSON content")
    try:
        payload = json.loads(normalized_content)
    except json.JSONDecodeError as exc:
        raise SummaryError("PARSE_JSON", format_parse_error(exc)) from exc
    if not isinstance(payload, dict):
        raise SummaryError("PARSE_OBJECT", "LLM did not return a JSON object")
    try:
        return AnnouncementSummary.model_validate(payload)
    except ValidationError as exc:
        raise SummaryError("PARSE_SCHEMA", format_parse_error(exc)) from exc


def normalize_summary_payload_text(raw_content: str) -> str:
    """清理 BOM 和偶发代码块包裹，保留真正的 JSON 文本。"""
    normalized = raw_content.strip().lstrip("\ufeff").strip()
    if not normalized:
        return ""
    fenced = unwrap_code_fence(normalized)
    return fenced.strip().lstrip("\ufeff").strip()


def unwrap_code_fence(raw_content: str) -> str:
    match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        raw_content,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return raw_content
    return match.group(1)


def format_repair_error(raw_content: str, exc: SummaryError | None) -> str:
    if exc is None:
        return "PARSE_UNKNOWN: unable to parse previous output"
    if raw_content.strip():
        return f"{exc.code}: {exc.message}"
    return f"{exc.code}: {exc.message}. The previous output was empty."


def should_attempt_repair(exc: SummaryError) -> bool:
    """只对格式和 schema 问题触发一次修复请求。"""
    return exc.code in REPAIRABLE_ERROR_CODES


def format_parse_error(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    return message
