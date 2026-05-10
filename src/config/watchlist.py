from __future__ import annotations

from pathlib import Path

import yaml

from domain.config_models import WatchlistConfig


def load_watchlist_config(config_path: str | Path) -> WatchlistConfig:
    """读取并校验 YAML 格式的观察列表配置。"""
    resolved_path = Path(config_path)
    if not resolved_path.exists():
        raise FileNotFoundError(f"watchlist config not found: {resolved_path}")
    payload = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("watchlist config must be a YAML object")
    return WatchlistConfig.model_validate(payload)
