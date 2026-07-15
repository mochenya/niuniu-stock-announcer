from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from db.records import AnnouncementCandidateRecord, DeliveryCandidateRecord
from domain.config_models import RuntimeConfig
from domain.workflow_models import AnnouncementRef
from log.events import LogEvent

# workflow 只依赖这个回调类型，不直接依赖 Loguru，便于 CLI 之外复用业务流程。
ProgressReporter = Callable[[LogEvent | str], None]


def noop_progress(_: LogEvent | str) -> None:
    return None


def require_database_url(runtime_config: RuntimeConfig) -> str:
    if not runtime_config.database_url:
        raise ValueError("WATCHLIST_DATABASE_URL cannot be empty")
    return runtime_config.database_url


def require_text(value: str | None, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


def dedupe_refs(refs: Sequence[AnnouncementRef]) -> list[AnnouncementRef]:
    deduped: list[AnnouncementRef] = []
    seen: set[str] = set()
    for ref in refs:
        if ref.key in seen:
            continue
        seen.add(ref.key)
        deduped.append(ref)
    return deduped


def dedupe_delivery_candidates(
    candidates: Sequence[DeliveryCandidateRecord],
) -> list[DeliveryCandidateRecord]:
    deduped: list[DeliveryCandidateRecord] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = f"{candidate.source}:{candidate.announcement_id}:{candidate.delivery_id}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def short_error(error: Exception | str, *, max_length: int = 100) -> str:
    text = " ".join(str(error).split()) or "unknown error"
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 1]}..."


def format_progress(index: int, total: int) -> str:
    width = max(len(str(total)), 2)
    return f"{index:0{width}d}/{total:0{width}d}"


def candidate_log_fields(candidate: AnnouncementCandidateRecord) -> dict[str, Any]:
    """抽取摘要/投递阶段最关键的公告上下文字段，保证各阶段日志口径一致。"""
    return {
        "stock": candidate.stock_code,
        "company": candidate.company_name,
        "source": candidate.source,
        "ann_id": candidate.announcement_id,
        "title": candidate.announcement.announcement_title,
    }
