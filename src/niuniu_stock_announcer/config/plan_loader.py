"""从单个 YAML 文件加载 typed China Plan。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from niuniu_stock_announcer.config.env import (
    EnvironmentReferenceError,
    load_plan_environment,
    resolve_environment_references,
)
from niuniu_stock_announcer.pipelines.china.schema import (
    ChinaPlan,
    MarketKeywordsPlan,
    SelectedStocksPlan,
)


class PlanLoadError(ValueError):
    """表示 Plan 在任何外部调用之前无法加载或校验。"""


class _PlanHeader(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    market: Literal["china"]
    plan_type: Literal["selected_stocks", "market_keywords"]


class _UniqueKeyLoader(yaml.SafeLoader):
    """让 YAML mapping 在进入 Pydantic 前拒绝重复 key。"""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"发现重复 YAML key: {key}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_china_plan(
    plan_path: str | Path,
    *,
    env_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ChinaPlan:
    """加载、解析环境引用并校验一份 China Plan。

    Args:
        plan_path: 必须存在、可读且为普通文件的单个 YAML Plan 路径。
        env_file: 可选 `.env` 路径；只为完整标量环境引用提供值。
        environ: 可注入环境映射；进程环境语义优先于 `.env`。

    Returns:
        `plan_type` 对应的冻结 Pydantic Plan Schema。

    Raises:
        PlanLoadError: 文件、YAML、环境引用、header 或完整 Schema 无效。
    """
    path = Path(plan_path)
    if not path.is_file():
        raise PlanLoadError(f"Plan 必须是可读的普通文件: {path}")
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise PlanLoadError(f"无法读取 Plan `{path}`: {_safe_yaml_error(exc)}") from exc
    if not isinstance(raw, dict):
        raise PlanLoadError("Plan 根节点必须是 YAML mapping")
    try:
        resolved = resolve_environment_references(
            raw,
            environment=load_plan_environment(
                env_file=env_file,
                environ=environ,
            ),
        )
    except EnvironmentReferenceError as exc:
        raise PlanLoadError(str(exc)) from exc
    if not isinstance(resolved, dict):
        raise PlanLoadError("Plan 根节点必须是 YAML mapping")
    try:
        header = _PlanHeader.model_validate(resolved)
        model = (
            SelectedStocksPlan
            if header.plan_type == "selected_stocks"
            else MarketKeywordsPlan
        )
        return model.model_validate(resolved)
    except ValidationError as exc:
        raise PlanLoadError(_format_validation_error(exc)) from exc


def _format_validation_error(exc: ValidationError) -> str:
    details: list[str] = []
    for error in exc.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in error["loc"]) or "$"
        details.append(f"{location}: {error['msg']}")
    return "Plan 校验失败: " + "; ".join(details)


def _safe_yaml_error(exc: Exception) -> str:
    if isinstance(exc, ConstructorError) and exc.problem:
        mark = exc.problem_mark
        location = ""
        if mark is not None:
            location = f"（第 {mark.line + 1} 行）"
        return f"{exc.problem}{location}"
    return exc.__class__.__name__
