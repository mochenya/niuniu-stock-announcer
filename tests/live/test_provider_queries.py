"""v2 公告 Provider 的六路真实查询与三份公开 PDF 契约。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest

from niuniu_stock_announcer.announcements.document import (
    AnnouncementDocumentService,
)
from niuniu_stock_announcer.announcements.providers.cninfo import (
    CninfoAnnouncementService,
)
from niuniu_stock_announcer.announcements.providers.sse import (
    SseAnnouncementService,
)
from niuniu_stock_announcer.announcements.providers.szse import (
    SzseAnnouncementService,
)
from niuniu_stock_announcer.announcements.schema import (
    AnnouncementQuery,
    CninfoSourceSnapshot,
    ProviderQueryResult,
    SseSourceSnapshot,
    SzseSourceSnapshot,
)

RecordLiveContract = Callable[[str], None]


@pytest.fixture(scope="module")
def live_provider_services() -> Iterator[
    tuple[dict[str, object], dict[tuple[object, ...], ProviderQueryResult]]
]:
    """构造 lazy v2 services，并在模块结束时关闭已创建 client。

    Yields:
        Provider service registry 与只在测试函数内填充的查询缓存。
    """
    services = {
        "cninfo": CninfoAnnouncementService(),
        "sse": SseAnnouncementService(),
        "szse": SzseAnnouncementService(),
    }
    cache: dict[tuple[object, ...], ProviderQueryResult] = {}
    try:
        yield services, cache
    finally:
        for service in services.values():
            service.close()


def _cached_query(
    live_provider_services,
    *,
    provider_key: str,
    query: AnnouncementQuery,
) -> ProviderQueryResult:
    services, cache = live_provider_services
    key = (
        provider_key,
        query.exchange,
        query.market_scope,
        query.start_date,
        query.end_date,
        query.stock_code,
        query.search_keyword,
        query.limit,
    )
    if key not in cache:
        cache[key] = services[provider_key].query(query)
    return cache[key]


@pytest.mark.live
@pytest.mark.parametrize(
    ("exchange", "scope", "stock", "expected_page_column"),
    [
        ("sh", "a_share", "688090", "SHKCB"),
        ("sz", "a_share", "000510", "SZZB"),
        ("bj", "a_share", "920717", "BJS"),
        ("hk", "hk", "06869", "HKZB"),
    ],
    ids=["cninfo-sh", "cninfo-sz", "cninfo-bj", "cninfo-hk"],
)
def test_cninfo_market_route_contract(
    exchange: str,
    scope: str,
    stock: str,
    expected_page_column: str,
    live_provider_services,
    record_live_contract: RecordLiveContract,
) -> None:
    result = _cached_query(
        live_provider_services,
        provider_key="cninfo",
        query=AnnouncementQuery(
            exchange=exchange,
            market_scope=scope,
            stock_code=stock,
            start_date=date(2026, 7, 13),
            end_date=date(2026, 8, 12),
            limit=1,
        ),
    )

    assert result.provider_key == "cninfo"
    assert len(result.items) == 1
    mapped = result.items[0]
    assert mapped.announcement.provider_key == "cninfo"
    assert mapped.announcement.provider_announcement_id
    assert mapped.announcement.market_scope == scope
    assert mapped.announcement.title
    assert mapped.announcement.source_url.startswith("https://static.cninfo.com.cn/")
    assert mapped.announcement.published_at.tzinfo is not None
    assert any(
        security.exchange == exchange and security.stock_code == stock
        for security in mapped.announcement.securities
    )
    snapshot = mapped.source_snapshot
    assert isinstance(snapshot, CninfoSourceSnapshot)
    assert snapshot.announcement_id == mapped.announcement.provider_announcement_id
    assert snapshot.announcement_time_ms is not None
    assert snapshot.adjunct_url
    assert snapshot.page_column == expected_page_column
    record_live_contract(
        f"provider=cninfo route={exchange} items={len(result.items)} "
        f"identity={mapped.announcement.provider_announcement_id}"
    )


@pytest.mark.live
def test_sse_keyword_route_contract(
    live_provider_services,
    record_live_contract: RecordLiveContract,
) -> None:
    result = _cached_query(
        live_provider_services,
        provider_key="sse",
        query=AnnouncementQuery(
            exchange="sh",
            market_scope="a_share",
            search_keyword="回购",
            start_date=date(2026, 8, 11),
            end_date=date(2026, 8, 12),
            limit=1,
        ),
    )

    assert result.provider_key == "sse"
    assert len(result.items) == 1
    mapped = result.items[0]
    assert mapped.announcement.provider_announcement_id.startswith("sse")
    assert mapped.announcement.source_url.startswith("https://static.sse.com.cn/")
    assert "回购" in mapped.announcement.title
    snapshot = mapped.source_snapshot
    assert isinstance(snapshot, SseSourceSnapshot)
    assert snapshot.provider_announcement_id == (
        mapped.announcement.provider_announcement_id
    )
    assert snapshot.org_bulletin_id
    assert snapshot.sse_date == "2026-08-12"
    assert snapshot.url and snapshot.url.endswith(".pdf")
    record_live_contract(
        f"provider=sse route=sh items={len(result.items)} "
        f"identity={mapped.announcement.provider_announcement_id}"
    )


@pytest.mark.live
def test_szse_stock_route_contract(
    live_provider_services,
    record_live_contract: RecordLiveContract,
) -> None:
    result = _cached_query(
        live_provider_services,
        provider_key="szse",
        query=AnnouncementQuery(
            exchange="sz",
            market_scope="a_share",
            stock_code="000510",
            start_date=date(2026, 7, 13),
            end_date=date(2026, 8, 12),
            limit=1,
        ),
    )

    assert result.provider_key == "szse"
    assert len(result.items) == 1
    mapped = result.items[0]
    assert mapped.announcement.provider_announcement_id
    assert mapped.announcement.source_url.startswith(
        "https://disc.static.szse.cn/download/"
    )
    assert any(
        security.exchange == "sz" and security.stock_code == "000510"
        for security in mapped.announcement.securities
    )
    snapshot = mapped.source_snapshot
    assert isinstance(snapshot, SzseSourceSnapshot)
    assert snapshot.provider_announcement_id == (
        mapped.announcement.provider_announcement_id
    )
    assert snapshot.ann_id is not None
    assert snapshot.sec_codes == ("000510",)
    assert snapshot.attach_path and snapshot.attach_path.endswith(".PDF")
    record_live_contract(
        f"provider=szse route=sz items={len(result.items)} "
        f"identity={mapped.announcement.provider_announcement_id}"
    )


@pytest.mark.live
def test_cninfo_pdf_contract(
    tmp_path: Path,
    live_provider_services,
    record_live_contract: RecordLiveContract,
) -> None:
    query_result = _cached_query(
        live_provider_services,
        provider_key="cninfo",
        query=AnnouncementQuery(
            exchange="sh",
            market_scope="a_share",
            stock_code="688090",
            start_date=date(2026, 7, 13),
            end_date=date(2026, 8, 12),
            limit=1,
        ),
    )
    services, _ = live_provider_services
    document = AnnouncementDocumentService(
        tmp_path, {"cninfo": services["cninfo"]}
    ).ensure_pdf(query_result.items[0].announcement)

    _assert_live_document(document, tmp_path, "cninfo")
    record_live_contract(
        f"provider=cninfo pdf_bytes={document.size_bytes} "
        f"pages={document.page_count} sha256={document.sha256[:12]}"
    )


@pytest.mark.live
def test_sse_pdf_contract(
    tmp_path: Path,
    live_provider_services,
    record_live_contract: RecordLiveContract,
) -> None:
    query_result = _cached_query(
        live_provider_services,
        provider_key="sse",
        query=AnnouncementQuery(
            exchange="sh",
            market_scope="a_share",
            search_keyword="回购",
            start_date=date(2026, 8, 11),
            end_date=date(2026, 8, 12),
            limit=1,
        ),
    )
    services, _ = live_provider_services
    document = AnnouncementDocumentService(
        tmp_path, {"sse": services["sse"]}
    ).ensure_pdf(query_result.items[0].announcement)

    _assert_live_document(document, tmp_path, "sse")
    record_live_contract(
        f"provider=sse pdf_bytes={document.size_bytes} "
        f"pages={document.page_count} sha256={document.sha256[:12]}"
    )


@pytest.mark.live
def test_szse_pdf_contract(
    tmp_path: Path,
    live_provider_services,
    record_live_contract: RecordLiveContract,
) -> None:
    query_result = _cached_query(
        live_provider_services,
        provider_key="szse",
        query=AnnouncementQuery(
            exchange="sz",
            market_scope="a_share",
            stock_code="000510",
            start_date=date(2026, 7, 13),
            end_date=date(2026, 8, 12),
            limit=1,
        ),
    )
    services, _ = live_provider_services
    document = AnnouncementDocumentService(
        tmp_path, {"szse": services["szse"]}
    ).ensure_pdf(query_result.items[0].announcement)

    _assert_live_document(document, tmp_path, "szse")
    record_live_contract(
        f"provider=szse pdf_bytes={document.size_bytes} "
        f"pages={document.page_count} sha256={document.sha256[:12]}"
    )


def _assert_live_document(document, tmp_path: Path, provider_key: str) -> None:
    assert document.provider_key == provider_key
    assert document.local_path.is_relative_to(tmp_path.resolve())
    assert document.storage_relative_path.startswith(f"{provider_key}/")
    assert document.local_path.read_bytes().startswith(b"%PDF-")
    assert document.size_bytes > 256
    assert document.page_count > 0
    assert len(document.sha256) == 64
