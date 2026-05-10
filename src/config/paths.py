from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_WATCHLIST_FILE = PROJECT_ROOT / "config" / "watchlist.yaml"


def resolve_env_file(env_file: str | Path | None) -> Path:
    """把 CLI 传入的 .env 路径解析为绝对路径。"""
    if env_file is None:
        return DEFAULT_ENV_FILE
    resolved = Path(env_file)
    if resolved.is_absolute():
        return resolved
    return (PROJECT_ROOT / resolved).resolve()


def resolve_project_path(
    value: str | None,
    *,
    default: Path,
    base_dir: Path,
) -> Path:
    """解析以 .env 所在目录为基准的项目路径配置。"""
    if not value:
        return default
    resolved = Path(value)
    if resolved.is_absolute():
        return resolved
    return (base_dir / resolved).resolve()
