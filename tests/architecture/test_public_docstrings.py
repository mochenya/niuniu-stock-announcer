"""跨层公共边界的中文 Google docstring 架构检查。"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable

from niuniu_stock_announcer.bootstrap import bootstrap
from niuniu_stock_announcer.config.env import (
    load_plan_environment,
    resolve_environment_references,
)
from niuniu_stock_announcer.config.plan_loader import load_china_plan
from niuniu_stock_announcer.config.settings import load_app_settings

PUBLIC_BOUNDARIES: tuple[Callable[..., object], ...] = (
    bootstrap,
    load_app_settings,
    load_plan_environment,
    resolve_environment_references,
    load_china_plan,
)


def test_registered_public_boundaries_use_chinese_google_docstrings() -> None:
    for boundary in PUBLIC_BOUNDARIES:
        docstring = inspect.getdoc(boundary) or ""
        assert re.search(r"[\u4e00-\u9fff]", docstring), boundary.__qualname__
        assert "Args:" in docstring, boundary.__qualname__
        for name, parameter in inspect.signature(boundary).parameters.items():
            if name in {"self", "cls"}:
                continue
            if parameter.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue
            assert re.search(rf"^\s*{re.escape(name)}:", docstring, re.MULTILINE), (
                boundary.__qualname__,
                name,
            )
