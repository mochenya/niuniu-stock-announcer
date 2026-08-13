"""v2 persistence owner 与 DDL 边界架构测试。"""

from pathlib import Path

from niuniu_stock_announcer.db.model import Base

PACKAGE_ROOT = Path("src/niuniu_stock_announcer")


def test_telegram_repository_does_not_import_market_models_or_plans() -> None:
    source = (PACKAGE_ROOT / "db/repositories/telegram.py").read_text(encoding="utf-8")
    assert "db.model.china" not in source
    assert "pipelines.china" not in source
    assert "Plan" not in source


def test_market_and_telegram_models_have_no_cross_owner_orm_relationships() -> None:
    assert len(Base.metadata.tables) == 9
    for mapper in Base.registry.mappers:
        assert not mapper.relationships


def test_business_runtime_never_calls_create_all_or_implicit_upgrade() -> None:
    explicit_database_boundaries = {"bootstrap.py", "cli.py", "migration.py"}
    runtime_files = [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "migrations" not in path.parts
        and path.name not in explicit_database_boundaries
    ]
    for path in runtime_files:
        source = path.read_text(encoding="utf-8")
        assert "create_all(" not in source, path
        assert "upgrade_database(" not in source, path

    for name in explicit_database_boundaries - {"migration.py"}:
        source = (PACKAGE_ROOT / name).read_text(encoding="utf-8")
        assert "create_all(" not in source, name
