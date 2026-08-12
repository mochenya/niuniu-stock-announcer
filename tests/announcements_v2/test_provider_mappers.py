"""v2 Provider Schema、mapper 与业务公告的离线契约。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from cninfo_announcement.models import CNInfoAnnouncementQueryResponse
from pydantic import ValidationError
from sse_announcement.models import SSEBulletinQueryResponse
from szse_announcement.models import (
    SZSEAnnouncementQueryResponse,
    SZSEAnnouncementRecord,
)

from niuniu_stock_announcer.announcements.providers.cninfo.mapper import (
    map_cninfo_announcement,
)
from niuniu_stock_announcer.announcements.providers.cninfo.schema import (
    CninfoNativeAnnouncement,
)
from niuniu_stock_announcer.announcements.providers.sse.mapper import (
    map_sse_announcement,
)
from niuniu_stock_announcer.announcements.providers.sse.schema import (
    SseNativeAnnouncement,
)
from niuniu_stock_announcer.announcements.providers.szse.mapper import (
    map_szse_announcement,
)
from niuniu_stock_announcer.announcements.providers.szse.schema import (
    SzseNativeAnnouncement,
)
from niuniu_stock_announcer.announcements.schema import AnnouncementQuery

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "providers"


def _load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _query(
    exchange: str,
    *,
    scope: str = "a_share",
    stock_code: str | None = None,
    search_keyword: str | None = None,
) -> AnnouncementQuery:
    return AnnouncementQuery(
        exchange=exchange,
        market_scope=scope,
        start_date=date(2026, 7, 13),
        end_date=date(2026, 8, 12),
        stock_code=stock_code,
        search_keyword=search_keyword,
    )


def test_cninfo_fixture_maps_strict_native_snapshot_and_business_identity() -> None:
    fixture = _load_fixture("cninfo_2026-08-12.json")
    response = CNInfoAnnouncementQueryResponse.model_validate(fixture["raw_response"])

    mapped = map_cninfo_announcement(
        response.announcements[0], _query("sh", stock_code="688090")
    )

    assert mapped.announcement.provider_key == "cninfo"
    assert mapped.announcement.provider_announcement_id == "1225450264"
    assert mapped.announcement.published_at == datetime(2026, 7, 31, 16, tzinfo=UTC)
    assert mapped.announcement.source_url == (
        "https://static.cninfo.com.cn/finalpage/2026-08-01/1225450264.PDF"
    )
    assert [item.model_dump() for item in mapped.announcement.securities] == [
        {"exchange": "sh", "stock_code": "688090", "stock_name": "瑞松科技"}
    ]
    snapshot = mapped.source_snapshot
    assert snapshot.provider_key == "cninfo"
    assert snapshot.announcement_time_ms == 1785513600000
    assert snapshot.page_column == "SHKCB"
    assert snapshot.announcement_type == "01010503||010123||011501||011507"


def test_sse_fixture_preserves_url_derived_identity_and_date_rule() -> None:
    fixture = _load_fixture("sse_2026-08-12.json")
    response = SSEBulletinQueryResponse.model_validate(fixture["raw_response"])

    mapped = map_sse_announcement(
        response.result[0][0], _query("sh", search_keyword="回购")
    )

    assert mapped.announcement.provider_announcement_id == "sse60052620260812VM75"
    assert mapped.announcement.published_at == datetime(2026, 8, 12, tzinfo=UTC)
    assert mapped.announcement.source_url == (
        "https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/"
        "2026-08-12/600526_20260812_VM75.pdf"
    )
    assert mapped.source_snapshot.org_bulletin_id == "6648117673246074"
    assert mapped.source_snapshot.sse_date == "2026-08-12"


def test_sse_mapper_rejects_attachment_instead_of_guessing_main_file() -> None:
    fixture = _load_fixture("sse_2026-08-12.json")
    response = SSEBulletinQueryResponse.model_validate(fixture["raw_response"])
    attachment = response.result[0][0].model_copy(update={"ORG_FILE_TYPE": 1})

    with pytest.raises(ValueError, match="ORG_FILE_TYPE=0"):
        map_sse_announcement(attachment, _query("sh", search_keyword="回购"))


def test_szse_fixture_preserves_native_arrays_and_aligned_business_securities() -> None:
    fixture = _load_fixture("szse_2026-08-12.json")
    response = SZSEAnnouncementQueryResponse.model_validate(fixture["raw_response"])

    mapped = map_szse_announcement(response.data[0], _query("sz", stock_code="000510"))

    assert mapped.announcement.provider_announcement_id == "1225426781"
    assert mapped.announcement.published_at.isoformat() == "2026-07-16T00:00:00+08:00"
    assert mapped.announcement.source_url == (
        "https://disc.static.szse.cn/download/disc/disk03/finalpage/2026-07-16/"
        "c361eaa5-df79-496a-bef9-693e6908ffca.PDF"
    )
    assert mapped.source_snapshot.ann_id == "1225426781"
    assert mapped.source_snapshot.sec_codes == ("000510",)
    assert mapped.source_snapshot.sec_names == ("新金路",)


def test_szse_multi_security_array_maps_once_and_keeps_positional_names() -> None:
    item = SZSEAnnouncementRecord(
        id="record-1",
        annId=123,
        title="多证券公告",
        publishTime="2026-08-12",
        attachPath="/disc/example.PDF",
        secCode=["000001", "000002", "000001"],
        secName=["平安银行", "万科A", "重复名称不覆盖"],
    )

    mapped = map_szse_announcement(item, _query("sz", stock_code="000001"))

    assert mapped.announcement.provider_announcement_id == "123"
    assert [item.model_dump() for item in mapped.announcement.securities] == [
        {"exchange": "sz", "stock_code": "000001", "stock_name": "平安银行"},
        {"exchange": "sz", "stock_code": "000002", "stock_name": "万科A"},
    ]
    assert mapped.source_snapshot.sec_codes == ("000001", "000002", "000001")


@pytest.mark.parametrize(
    "schema",
    [CninfoNativeAnnouncement, SseNativeAnnouncement, SzseNativeAnnouncement],
)
def test_provider_native_schemas_reject_unreviewed_fields(schema) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        schema.model_validate({"unreviewed_field": "must fail"})
