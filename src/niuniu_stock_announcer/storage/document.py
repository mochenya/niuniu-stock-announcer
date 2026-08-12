"""本地 document 路径边界。"""

from pathlib import Path, PurePosixPath
from typing import Annotated

from pydantic import AfterValidator


def validate_storage_relative_path(value: str) -> str:
    """校验并规范化 storage root 下的 POSIX 相对路径。

    Args:
        value: 准备写入数据库的相对路径文本。

    Returns:
        可安全拼接到 storage root 的规范 POSIX 路径。

    Raises:
        ValueError: 路径为空、带 URI scheme、为绝对路径或包含越界片段。
    """
    normalized = value.strip()
    if not normalized:
        raise ValueError("storage_relative_path 不能为空")
    if normalized in {".", ".."}:
        raise ValueError("storage_relative_path 不能是当前目录或父目录")
    if "\\" in normalized:
        raise ValueError("storage_relative_path 必须使用 POSIX 分隔符")
    first_segment = normalized.split("/", maxsplit=1)[0]
    if ":" in first_segment:
        raise ValueError("storage_relative_path 不能包含 URI scheme")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("storage_relative_path 必须是 storage root 内的相对路径")
    if path.as_posix() != normalized:
        raise ValueError("storage_relative_path 必须是规范 POSIX 路径")
    return normalized


StorageRelativePath = Annotated[str, AfterValidator(validate_storage_relative_path)]


def resolve_storage_path(root: Path, relative_path: StorageRelativePath) -> Path:
    """把已校验相对路径解析到 storage root 内。

    Args:
        root: 本地 document storage 根目录。
        relative_path: 已通过 Pydantic 边界校验的 POSIX 相对路径。

    Returns:
        解析后的本地绝对路径。

    Raises:
        ValueError: 符号链接或路径解析使结果越出 storage root。
    """
    resolved_root = root.resolve()
    resolved = (resolved_root / relative_path).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("storage_relative_path 解析后越出 storage root")
    return resolved
