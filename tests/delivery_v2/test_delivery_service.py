"""纯 Delivery Service 的冻结渲染与本地文件边界测试。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from niuniu_stock_announcer.db.schema import (
    ChinaAnnouncementRecord,
    ChinaMatchRecord,
    ChinaSummaryRecord,
    ChinaSummaryRenderContext,
    PdfSnapshot,
    TelegramDeliveryRecord,
)
from niuniu_stock_announcer.delivery.document import open_verified_document
from niuniu_stock_announcer.delivery.service import DeliveryService
from niuniu_stock_announcer.im.telegram.schema import TelegramDocumentSendRequest
from niuniu_stock_announcer.storage.document import validate_storage_relative_path
from tests.db_v2.factories import (
    announcement,
    delivery,
    keyword_match,
    selected_match,
    summary_completion,
)

NOW = datetime(2026, 8, 13, 8, tzinfo=UTC)


def _context(
    *,
    status: str = "completed",
    plan_key: str = "selected-plan",
    send_pdf: bool = True,
    keyword: bool = False,
) -> tuple[ChinaSummaryRenderContext, TelegramDeliveryRecord]:
    announcement_write = announcement("render-1")
    pdf = PdfSnapshot(
        storage_relative_path="cninfo/2026/08/render-1.pdf",
        size_bytes=4096,
        sha256="a" * 64,
    )
    announcement_record = ChinaAnnouncementRecord(
        **announcement_write.model_dump(mode="python"),
        id=11,
        pdf=pdf,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    match_write = (
        keyword_match(11, plan_key=plan_key, keywords=("回购", "中标"))
        if keyword
        else selected_match(11, plan_key=plan_key)
    )
    match = ChinaMatchRecord(
        **match_write.model_dump(mode="python"),
        id=21,
        first_seen_at=NOW,
        last_seen_at=NOW,
        hit_count=1,
    )
    completion = summary_completion("公司拟回购股份 <待审议>")
    summary = ChinaSummaryRecord(
        id=31,
        china_announcement_id=11,
        status=status,
        failure_count=3 if status == "skipped" else 0,
        agent_key=completion.agent_key if status == "completed" else None,
        agent_version=completion.agent_version if status == "completed" else None,
        prompt_version=completion.prompt_version if status == "completed" else None,
        model_provider=completion.model_provider if status == "completed" else None,
        model_name=completion.model_name if status == "completed" else None,
        input_tokens=completion.input_tokens if status == "completed" else None,
        output_tokens=completion.output_tokens if status == "completed" else None,
        result=completion.result if status == "completed" else None,
        failure_reason="摘要重试耗尽" if status == "skipped" else None,
        failure_log="trace" if status == "skipped" else None,
        started_at=NOW,
        finished_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    delivery_write = delivery(
        summary.id,
        plan_key=plan_key,
        send_original_document=send_pdf,
    )
    parent = TelegramDeliveryRecord(
        **delivery_write.model_dump(mode="python"),
        id=41,
        created_at=NOW,
    )
    return (
        ChinaSummaryRenderContext(
            summary=summary,
            announcement=announcement_record,
            selected_matches=(match,),
        ),
        parent,
    )


def test_delivery_service_formats_html_and_freezes_optional_document() -> None:
    context, parent = _context(send_pdf=True)

    materialized = DeliveryService().render(context, parent)

    text = materialized.summary.text_content
    assert "测试公告 render-1" in text
    assert "瑞松科技" in text
    assert "#SH688090" in text
    assert "公司拟回购股份 &lt;待审议&gt;" in text
    assert "#股份回购 #回购进展 #A股" in text
    assert len(materialized.documents) == 1
    document = materialized.documents[0]
    assert document.storage_relative_path == "cninfo/2026/08/render-1.pdf"
    assert document.document_filename == "render-1.pdf"
    assert document.document_caption == "\n".join(text.splitlines()[:5])

    no_pdf_context, no_pdf_parent = _context(send_pdf=False)
    assert DeliveryService().render(no_pdf_context, no_pdf_parent).documents == ()


def test_skipped_forces_document_and_keyword_evidence_without_stock_requirement() -> (
    None
):
    context, parent = _context(
        status="skipped",
        send_pdf=False,
        plan_key="keyword-plan",
        keyword=True,
    )

    materialized = DeliveryService().render(context, parent)

    assert "摘要生成失败，请直接查看 PDF" in materialized.summary.text_content
    assert "keyword #回购 keyword #中标" in materialized.summary.text_content
    assert len(materialized.documents) == 1


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        ".",
        "..",
        "/absolute.pdf",
        "file:document.pdf",
        "https://example.invalid/document.pdf",
        "folder/../document.pdf",
        "folder//document.pdf",
        r"folder\document.pdf",
    ],
)
def test_invalid_storage_relative_paths_are_rejected(relative_path: str) -> None:
    with pytest.raises(ValueError):
        validate_storage_relative_path(relative_path)


def _request(content: bytes, *, relative_path: str = "docs/announcement.pdf"):
    return TelegramDocumentSendRequest(
        target={"chat_id": -100123, "message_thread_id": 9},
        storage_relative_path=relative_path,
        document_filename="公告.pdf",
        document_size_bytes=len(content),
        document_sha256=hashlib.sha256(content).hexdigest(),
        document_caption="caption",
    )


def test_open_verified_document_checks_same_handle_size_and_hash(
    tmp_path: Path,
) -> None:
    content = b"%PDF-1.7\nverified\n%%EOF"
    path = tmp_path / "docs/announcement.pdf"
    path.parent.mkdir()
    path.write_bytes(content)
    request = _request(content)

    with open_verified_document(request, storage_root=tmp_path) as document_file:
        assert document_file.read() == content

    with pytest.raises(ValueError, match="大小"):
        with open_verified_document(
            request.model_copy(update={"document_size_bytes": len(content) + 1}),
            storage_root=tmp_path,
        ):
            pass
    with pytest.raises(ValueError, match="SHA-256"):
        with open_verified_document(
            request.model_copy(update={"document_sha256": "0" * 64}),
            storage_root=tmp_path,
        ):
            pass


def test_open_verified_document_rejects_missing_and_symlink_escape(
    tmp_path: Path,
) -> None:
    content = b"%PDF-1.7\n%%EOF"
    with pytest.raises(FileNotFoundError, match="不存在"):
        with open_verified_document(_request(content), storage_root=tmp_path):
            pass

    outside = tmp_path.parent / f"{tmp_path.name}-outside.pdf"
    outside.write_bytes(content)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "announcement.pdf").symlink_to(outside)
    try:
        with pytest.raises(ValueError, match="越出"):
            with open_verified_document(_request(content), storage_root=tmp_path):
                pass
    finally:
        outside.unlink()
