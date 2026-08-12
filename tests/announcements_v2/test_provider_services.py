"""三个 Provider Service 的 lazy client、查询参数和错误隔离测试。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from cninfo_announcement.models import CNInfoAnnouncementQueryResponse
from sse_announcement.models import SSEBulletinQueryResponse
from szse_announcement.models import SZSEAnnouncementQueryResponse

from niuniu_stock_announcer.announcements.providers.cninfo.service import (
    CninfoAnnouncementService,
)
from niuniu_stock_announcer.announcements.providers.sse.service import (
    SseAnnouncementService,
)
from niuniu_stock_announcer.announcements.providers.szse.service import (
    SzseAnnouncementService,
)
from niuniu_stock_announcer.announcements.schema import (
    AnnouncementQuery,
    ChinaAnnouncement,
)

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "providers"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class _FakeClient:
    def __init__(self, native_items: list[object], *, has_more: bool = False) -> None:
        self.native_items = native_items
        self.has_more = has_more
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.download_calls: list[tuple[object, Path]] = []
        self.closed = False

    def query_announcements(self, *args: object, **kwargs: object):
        self.calls.append((args, kwargs))
        return SimpleNamespace(
            response=SimpleNamespace(
                announcements=self.native_items,
                has_more=self.has_more,
            )
        )

    def download_pdf(self, announcement, *, save_dir: Path) -> Path:
        self.download_calls.append((announcement, save_dir))
        return save_dir / f"{announcement.announcement_id}.pdf"

    def close(self) -> None:
        self.closed = True


def _query(
    exchange: str,
    *,
    stock_code: str | None = None,
    search_keyword: str | None = None,
    limit: int | None = None,
) -> AnnouncementQuery:
    return AnnouncementQuery(
        exchange=exchange,
        market_scope="a_share",
        start_date=date(2026, 8, 11),
        end_date=date(2026, 8, 12),
        stock_code=stock_code,
        search_keyword=search_keyword,
        limit=limit,
    )


def _cninfo_item():
    raw = CNInfoAnnouncementQueryResponse.model_validate(
        _fixture("cninfo_2026-08-12.json")["raw_response"]
    )
    return raw.announcements[0]


def _sse_item():
    raw = SSEBulletinQueryResponse.model_validate(
        _fixture("sse_2026-08-12.json")["raw_response"]
    )
    return raw.result[0][0]


def _szse_item():
    raw = SZSEAnnouncementQueryResponse.model_validate(
        _fixture("szse_2026-08-12.json")["raw_response"]
    )
    return raw.data[0]


def test_cninfo_service_is_lazy_and_translates_stock_query() -> None:
    created: list[_FakeClient] = []

    def factory() -> _FakeClient:
        client = _FakeClient([_cninfo_item()])
        created.append(client)
        return client

    service = CninfoAnnouncementService(factory)
    assert service.provider_key == "cninfo"
    assert created == []

    result = service.query(_query("sh", stock_code="688090", limit=1))

    assert len(created) == 1
    assert result.provider_key == "cninfo"
    assert len(result.items) == 1
    assert created[0].calls == [
        (
            ("sh",),
            {
                "start_date": date(2026, 8, 11),
                "end_date": date(2026, 8, 12),
                "stock": "688090",
            },
        )
    ]
    service.close()
    assert created[0].closed is True


def test_cninfo_service_truncates_after_mapping_and_isolates_invalid_item() -> None:
    invalid = _cninfo_item().model_copy(update={"announcementTitle": None})
    client = _FakeClient([invalid, _cninfo_item(), _cninfo_item()])
    service = CninfoAnnouncementService(lambda: client)

    result = service.query(_query("sh", search_keyword="回购", limit=1))

    assert len(result.items) == 1
    assert len(result.item_errors) == 1
    assert result.item_errors[0].item_index == 0
    assert result.has_more is True
    assert client.calls[0][1]["searchkey"] == "回购"
    assert "stock" not in client.calls[0][1]


@pytest.mark.parametrize(
    ("service_type", "client", "exchange", "provider_key", "query", "native_item"),
    [
        (
            SseAnnouncementService,
            _FakeClient([_sse_item()], has_more=True),
            "sh",
            "sse",
            _query("sh", search_keyword="回购", limit=1),
            _sse_item(),
        ),
        (
            SzseAnnouncementService,
            _FakeClient([_szse_item()]),
            "sz",
            "szse",
            _query("sz", stock_code="000510", limit=1),
            _szse_item(),
        ),
    ],
)
def test_exchange_services_forward_query_shape_and_limit(
    service_type,
    client: _FakeClient,
    exchange: str,
    provider_key: str,
    query: AnnouncementQuery,
    native_item: object,
) -> None:
    service = service_type(lambda: client)

    result = service.query(query)

    assert result.provider_key == provider_key
    assert len(result.items) == 1
    assert result.items[0].announcement.provider_key == provider_key
    assert client.native_items == [native_item]
    assert client.calls == [
        (
            (),
            {
                "stock": query.stock_code,
                "searchkey": query.search_keyword,
                "start_date": query.start_date,
                "end_date": query.end_date,
                "limit": 1,
            },
        )
    ]
    if provider_key == "sse":
        assert result.has_more is True
    assert query.exchange == exchange


@pytest.mark.parametrize(
    ("service", "query", "message"),
    [
        (
            SseAnnouncementService(lambda: _FakeClient([])),
            _query("sz", stock_code="000510"),
            "exchange=sh",
        ),
        (
            SzseAnnouncementService(lambda: _FakeClient([])),
            _query("sh", stock_code="688090"),
            "exchange=sz",
        ),
    ],
)
def test_exchange_service_rejects_wrong_route_before_creating_client(
    service, query: AnnouncementQuery, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        service.query(query)


@pytest.mark.parametrize(
    ("service_type", "provider_key", "source_url"),
    [
        (
            CninfoAnnouncementService,
            "cninfo",
            "https://static.cninfo.com.cn/finalpage/a.PDF",
        ),
        (
            SseAnnouncementService,
            "sse",
            "https://static.sse.com.cn/disclosure/a.pdf",
        ),
        (
            SzseAnnouncementService,
            "szse",
            "https://disc.static.szse.cn/download/disc/a.PDF",
        ),
    ],
)
def test_service_download_bridge_preserves_document_target_path(
    tmp_path: Path,
    service_type,
    provider_key: str,
    source_url: str,
) -> None:
    client = _FakeClient([])
    service = service_type(lambda: client)
    target_path = tmp_path / "nested" / "stable-id.pdf"
    announcement = ChinaAnnouncement(
        provider_key=provider_key,
        provider_announcement_id="native-id",
        market_scope="a_share",
        title="测试公告",
        published_at="2026-08-12T00:00:00Z",
        source_url=source_url,
    )

    result = service.download_pdf(announcement, target_path=target_path)

    assert result == target_path
    sdk_announcement, save_dir = client.download_calls[0]
    assert sdk_announcement.source.value == provider_key
    assert sdk_announcement.announcement_id == "stable-id"
    assert sdk_announcement.adjunct_url == source_url
    assert save_dir == target_path.parent
