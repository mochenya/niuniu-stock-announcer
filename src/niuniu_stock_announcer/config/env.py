"""解析 Plan 中受控的环境变量标量引用。"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

ENV_REFERENCE_PATTERN = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")
UNSUPPORTED_REFERENCE_PATTERN = re.compile(r"\$\{|\{[A-Z][A-Z0-9_]*\}")


class EnvironmentReferenceError(ValueError):
    """表示 Plan 环境引用在完整 Schema 校验前失败。"""


def load_plan_environment(
    *,
    env_file: str | Path | None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """加载 Plan 可引用的环境值，并让进程环境覆盖 `.env`。

    Args:
        env_file: 显式 `.env` 路径；为 `None` 时读取当前目录的 `.env`（若存在）。
        environ: 可注入的进程环境映射；测试可传入隔离值。

    Returns:
        合并后的纯字符串环境映射。
    """
    path = Path(".env") if env_file is None else Path(env_file)
    from_file: Mapping[str, str | None] = {}
    if path.is_file():
        # 禁止 dotenv 自己递归展开 `${...}`，Plan resolver 只允许一次完整标量替换。
        from_file = dotenv_values(path, interpolate=False)
    values = {key: value for key, value in from_file.items() if isinstance(value, str)}
    values.update(dict(os.environ if environ is None else environ))
    return values


def resolve_environment_references(
    payload: object, *, environment: Mapping[str, str]
) -> object:
    """递归解析 YAML 中完整标量形式的 `${ENV_NAME}`。

    Args:
        payload: `yaml.safe_load` 产生的原始结构。
        environment: `.env` 与进程环境按优先级合并后的值。

    Returns:
        不修改输入对象的新结构；环境值不会再次递归展开。

    Raises:
        EnvironmentReferenceError: 引用格式非法或变量缺失。
    """
    return _resolve_node(payload, environment=environment, path="$")


def _resolve_node(
    value: object, *, environment: Mapping[str, str], path: str
) -> object:
    if isinstance(value, dict):
        return {
            key: _resolve_node(
                item,
                environment=environment,
                path=f"{path}.{key}",
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_node(
                item,
                environment=environment,
                path=f"{path}[{index}]",
            )
            for index, item in enumerate(value)
        ]
    if not isinstance(value, str):
        return value
    match = ENV_REFERENCE_PATTERN.fullmatch(value)
    if match is not None:
        name = match.group(1)
        resolved = environment.get(name)
        if resolved is None:
            raise EnvironmentReferenceError(f"{path}: 缺少环境变量 `{name}`")
        return resolved
    if UNSUPPORTED_REFERENCE_PATTERN.search(value):
        raise EnvironmentReferenceError(
            f"{path}: 环境引用必须是完整标量 `${{ENV_NAME}}`"
        )
    return value
