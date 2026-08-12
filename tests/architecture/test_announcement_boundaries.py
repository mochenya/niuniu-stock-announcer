"""公告 Provider、Pipeline 与持久化层的依赖方向门禁。"""

from pathlib import Path

PACKAGE_ROOT = Path("src/niuniu_stock_announcer")
PROVIDER_ROOT = PACKAGE_ROOT / "announcements/providers"
PIPELINE_ROOT = PACKAGE_ROOT / "pipelines/china"
SDK_IMPORT_PREFIXES = (
    "announcement_common.",
    "cninfo_announcement.",
    "sse_announcement.",
    "szse_announcement.",
)


def test_provider_native_schemas_and_sdks_stay_inside_provider_adapters() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        if path.is_relative_to(PROVIDER_ROOT):
            continue
        source = path.read_text(encoding="utf-8")
        assert ".announcements.providers.cninfo.schema" not in source, path
        assert ".announcements.providers.sse.schema" not in source, path
        assert ".announcements.providers.szse.schema" not in source, path
        for prefix in SDK_IMPORT_PREFIXES:
            assert f"from {prefix}" not in source, path
            assert f"import {prefix}" not in source, path


def test_provider_adapters_do_not_depend_on_plan_database_or_runtime_config() -> None:
    for path in PROVIDER_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "niuniu_stock_announcer.config" not in source, path
        assert "niuniu_stock_announcer.db" not in source, path
        assert "niuniu_stock_announcer.pipelines" not in source, path
        assert "sqlalchemy" not in source, path


def test_china_pipeline_never_imports_sdk_or_orm_models() -> None:
    for path in PIPELINE_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "niuniu_stock_announcer.db.model" not in source, path
        assert "sqlalchemy" not in source, path
        for prefix in SDK_IMPORT_PREFIXES:
            assert f"from {prefix}" not in source, path
            assert f"import {prefix}" not in source, path
