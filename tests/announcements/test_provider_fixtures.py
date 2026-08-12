"""锁定版本 SDK 与 2026-08-12 真实响应 fixture 的映射契约。"""

from __future__ import annotations

import json
from pathlib import Path

from cninfo_announcement.client import CNInfoClient
from cninfo_announcement.models import CNInfoAnnouncementQueryResponse
from sse_announcement.client import SSEAnnouncementClient
from sse_announcement.models import SSEBulletinQueryResponse
from szse_announcement.client import SZSEAnnouncementClient
from szse_announcement.models import SZSEAnnouncementQueryResponse

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "providers"


def _load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_cninfo_fixture_preserves_identity_time_and_native_fields() -> None:
    fixture = _load_fixture("cninfo_2026-08-12.json")
    raw = CNInfoAnnouncementQueryResponse.model_validate(fixture["raw_response"])
    announcement = raw.announcements[0]
    client = object.__new__(CNInfoClient)
    business = client._to_business_announcement(announcement)

    assert business.announcement_id == "1225450264"
    assert business.announcement_time == 1785513600000
    assert business.sec_code == "688090"
    assert business.page_column == "SHKCB"
    assert announcement.adjunctSize == 87
    assert announcement.announcementType == "01010503||010123||011501||011507"


def test_sse_fixture_preserves_group_shape_and_url_derived_identity() -> None:
    fixture = _load_fixture("sse_2026-08-12.json")
    raw = SSEBulletinQueryResponse.model_validate(fixture["raw_response"])
    announcement = raw.result[0][0]
    business = SSEAnnouncementClient._to_business_announcement(None, announcement)

    assert len(raw.result) == 1
    assert business.announcement_id == "sse60052620260812VM75"
    assert business.announcement_time == 1786492800000
    assert business.org_id == "6648117673246074"
    assert business.adjunct_url.endswith("600526_20260812_VM75.pdf")


def test_szse_fixture_preserves_array_shape_and_integer_native_id() -> None:
    fixture = _load_fixture("szse_2026-08-12.json")
    raw = SZSEAnnouncementQueryResponse.model_validate(fixture["raw_response"])
    announcement = raw.data[0]
    business = SZSEAnnouncementClient._to_business_announcement(None, announcement)

    assert announcement.annId == 1225426781
    assert announcement.secCode == ["000510"]
    assert announcement.secName == ["新金路"]
    assert business.announcement_id == "1225426781"
    assert business.announcement_time == 1784131200000
    assert business.org_id == "szse000510"


def test_fixtures_contain_only_reviewed_contract_metadata() -> None:
    forbidden_fragments = {
        "authorization",
        "cookie",
        "set-cookie",
        "user-agent",
        "api_key",
        "bot_token",
    }

    for path in FIXTURE_DIR.glob("*.json"):
        text = path.read_text(encoding="utf-8").lower()
        assert not forbidden_fragments.intersection(text.split('"'))
