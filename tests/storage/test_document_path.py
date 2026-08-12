"""document storage 相对路径安全测试。"""

from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from niuniu_stock_announcer.storage.document import (
    StorageRelativePath,
    resolve_storage_path,
)


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "/absolute/a.pdf",
        "../a.pdf",
        "a/../b.pdf",
        "a/./b.pdf",
        "a//b.pdf",
        "https://example.invalid/a.pdf",
        "file:a.pdf",
        "C:/a.pdf",
        "a\\b.pdf",
    ],
)
def test_storage_relative_path_rejects_unsafe_or_noncanonical_value(
    value: str,
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(StorageRelativePath).validate_python(value)


def test_storage_relative_path_resolves_inside_root(tmp_path: Path) -> None:
    value = TypeAdapter(StorageRelativePath).validate_python(
        "cninfo/2026/08/announcement.pdf"
    )
    assert (
        resolve_storage_path(tmp_path, value)
        == (tmp_path / "cninfo/2026/08/announcement.pdf").resolve()
    )


def test_storage_path_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-storage"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "linked"
    link.symlink_to(outside, target_is_directory=True)
    value = TypeAdapter(StorageRelativePath).validate_python("linked/a.pdf")
    with pytest.raises(ValueError, match="越出 storage root"):
        resolve_storage_path(tmp_path, value)
