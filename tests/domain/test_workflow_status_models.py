from __future__ import annotations

import sys
from pathlib import Path
from typing import get_args

import pytest
from cninfo_announcement.models import BusinessAnnouncement
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import domain.common as common  # noqa: E402
from domain.workflow_models import WorkflowCandidate  # noqa: E402


def test_stage_status_literals_are_specific_to_their_workflow_stage() -> None:
    assert set(get_args(common.SummaryStatus)) == {
        "pending",
        "running",
        "completed",
        "failed",
        "skipped",
    }
    assert set(get_args(common.DeliveryStatus)) == {
        "pending",
        "running",
        "completed",
        "failed",
        "unknown",
    }


def test_summary_status_rejects_delivery_only_unknown() -> None:
    with pytest.raises(ValidationError):
        WorkflowCandidate.model_validate(_candidate_payload(summary_status="unknown"))


def test_delivery_status_rejects_summary_only_skipped() -> None:
    with pytest.raises(ValidationError):
        WorkflowCandidate.model_validate(_candidate_payload(delivery_status="skipped"))


def _candidate_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source": "cninfo",
        "announcement_id": "ann-1",
        "announcement": BusinessAnnouncement(
            source="cninfo",
            sec_code="600000",
            sec_name="测试公司",
            announcement_id="ann-1",
            announcement_title="测试公告",
            announcement_time=1,
        ),
        "market": "sh",
        "stock_code": "600000",
        "stock_key": "sh:600000",
        "company_name": "测试公司",
    }
    payload.update(overrides)
    return payload
