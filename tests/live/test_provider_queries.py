"""三个公告 Provider 的六路真实查询契约。"""

from __future__ import annotations

from datetime import date

import pytest
from cninfo_announcement.client import CNInfoClient
from sse_announcement.client import SSEAnnouncementClient
from szse_announcement.client import SZSEAnnouncementClient


@pytest.mark.live
@pytest.mark.parametrize(
    ("market", "stock", "expected_scope_marker"),
    [
        ("sh", "688090", "SH"),
        ("sz", "000510", "SZ"),
        ("bj", "920717", "BJ"),
        ("hk", "06869", "HK"),
    ],
    ids=["cninfo-sh", "cninfo-sz", "cninfo-bj", "cninfo-hk"],
)
def test_cninfo_market_route_contract(
    market: str, stock: str, expected_scope_marker: str
) -> None:
    with CNInfoClient(retries=1) as client:
        result = client.query_announcements(
            market,
            stock=stock,
            start_date=date(2026, 7, 13),
            end_date=date(2026, 8, 12),
        )

    assert result.source.value == "cninfo"
    assert result.items
    assert result.response.raw_responses
    raw = result.response.raw_responses[0]
    assert raw.announcements
    native = raw.announcements[0]
    business = result.items[0]
    assert native.announcementId == business.announcement_id
    assert native.announcementTime == business.announcement_time
    assert native.adjunctUrl == business.adjunct_url
    assert native.secCode == stock
    assert expected_scope_marker in (native.pageColumn or "").upper()
    assert business.announcement_id
    assert business.announcement_title
    assert business.adjunct_url


@pytest.mark.live
def test_sse_keyword_route_contract() -> None:
    with SSEAnnouncementClient(retries=1) as client:
        result = client.query_announcements(
            searchkey="回购",
            start_date=date(2026, 8, 11),
            end_date=date(2026, 8, 12),
            limit=1,
        )

    assert result.source.value == "sse"
    assert len(result.items) == 1
    assert result.response.raw_responses
    raw = result.response.raw_responses[0]
    assert raw.result and raw.result[0]
    main_files = [item for item in raw.result[0] if item.ORG_FILE_TYPE == 0]
    assert len(main_files) == 1
    native = main_files[0]
    business = result.items[0]
    assert business.sec_code == native.SECURITY_CODE
    assert business.org_id == native.ORG_BULLETIN_ID
    assert business.announcement_id.startswith("sse")
    assert native.SSEDATE == "2026-08-12"
    assert business.adjunct_url and business.adjunct_url.endswith(".pdf")


@pytest.mark.live
def test_szse_stock_route_contract() -> None:
    with SZSEAnnouncementClient(retries=1) as client:
        result = client.query_announcements(
            stock="000510",
            start_date=date(2026, 7, 13),
            end_date=date(2026, 8, 12),
            limit=1,
        )

    assert result.source.value == "szse"
    assert len(result.items) == 1
    assert result.response.raw_responses
    raw = result.response.raw_responses[0]
    assert raw.data
    native = raw.data[0]
    business = result.items[0]
    assert isinstance(native.annId, int)
    assert native.secCode == ["000510"]
    assert business.announcement_id == str(native.annId)
    assert business.sec_code == "000510"
    assert business.page_column == "listedNotice_disc"
    assert business.adjunct_url and business.adjunct_url.endswith(".PDF")
