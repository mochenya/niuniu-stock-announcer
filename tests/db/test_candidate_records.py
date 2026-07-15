from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from db.row_mappers import (  # noqa: E402
    build_delivery_candidate_record,
    build_summary_candidate_record,
)


def _common_row() -> dict[str, object]:
    return {
        "source": "cninfo",
        "announcement_id": "ann-1",
        "sec_code": "600000",
        "sec_name": "测试公司",
        "org_id": "org-1",
        "announcement_title": "测试公告",
        "announcement_time_ms": 1,
        "adjunct_url": "test.pdf",
        "page_column": "公告",
        "market": "sh",
        "stock_code": "600000",
        "stock_key": "sh:600000",
        "company_name": "测试公司",
        "search_keyword": None,
    }


def test_summary_mapper_exposes_only_summary_fields() -> None:
    row = _common_row() | {
        "pdf_local_path": None,
        "summary_failure_count": 2,
        "primary_hit_id": 11,
        "summary_json": {"summary": "old"},
    }

    record = build_summary_candidate_record(row)

    assert record.ref.key == "cninfo:ann-1"
    assert record.pdf_local_path is None
    assert record.summary_failure_count == 2
    assert not hasattr(record, "summary_json")
    assert not hasattr(record, "delivery_id")


def test_delivery_mapper_builds_stored_summary_and_delivery_context() -> None:
    row = _common_row() | {
        "summary_status": "completed",
        "pdf_local_path": "/tmp/ann-1.pdf",
        "summary_text": "测试摘要",
        "summary_tags": ["标签一", "标签二", "标签三"],
        "delivery_id": 7,
        "target_key": "a_share",
        "text_message_id": 101,
        "pdf_message_id": None,
    }

    record = build_delivery_candidate_record(row)

    assert record.pdf_local_path == Path("/tmp/ann-1.pdf")
    assert record.delivery_id == 7
    assert record.target_key == "a_share"
    assert record.stored_summary is not None
    assert record.stored_summary.tags == ["标签一", "标签二", "标签三"]
    assert not hasattr(record, "summary_failure_count")


def test_delivery_mapper_requires_pdf_path() -> None:
    row = _common_row() | {
        "summary_status": "completed",
        "pdf_local_path": None,
        "summary_text": "测试摘要",
        "summary_tags": ["标签一", "标签二", "标签三"],
        "delivery_id": 7,
        "target_key": "a_share",
        "text_message_id": None,
        "pdf_message_id": None,
    }

    with pytest.raises(ValueError, match="pdf_local_path"):
        build_delivery_candidate_record(row)
