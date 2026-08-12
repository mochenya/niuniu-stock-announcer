"""共享 Delivery 与 Telegram adapter 的依赖方向门禁。"""

from pathlib import Path

PACKAGE_ROOT = Path("src/niuniu_stock_announcer")
TELEGRAM_ROOT = PACKAGE_ROOT / "im/telegram"


def test_telegram_adapter_does_not_import_plan_database_or_china_pipeline() -> None:
    for path in TELEGRAM_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "niuniu_stock_announcer.config" not in source, path
        assert "niuniu_stock_announcer.db" not in source, path
        assert "niuniu_stock_announcer.pipelines" not in source, path
        assert "sqlalchemy" not in source, path
        assert "plan_loader" not in source, path
        assert "delivery.service" not in source, path


def test_delivery_recovery_uses_frozen_outbox_without_plan_or_china_reads() -> None:
    source = (PACKAGE_ROOT / "pipelines/china/stages/delivery.py").read_text(
        encoding="utf-8"
    )
    assert "plan_loader" not in source
    assert "ChinaSummaryRenderContext" not in source
    assert "china_summaries" not in source
    assert "send_original_document" not in source
    assert "format_telegram" not in source


def test_document_sender_has_no_remote_url_fallback() -> None:
    sender_source = (TELEGRAM_ROOT / "sender.py").read_text(encoding="utf-8")
    sender_schema = (TELEGRAM_ROOT / "schema.py").read_text(encoding="utf-8")
    assert "source_url" not in sender_source
    assert "source_url" not in sender_schema
    assert "httpx" not in sender_source
    assert "requests" not in sender_source


def test_run_log_adapter_has_no_outbox_or_repository_dependency() -> None:
    source = (TELEGRAM_ROOT / "run_log.py").read_text(encoding="utf-8")
    assert "db.repositories" not in source
    assert "UnitOfWork" not in source
    assert "uow.telegram" not in source
