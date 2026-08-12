"""投递阶段发送前的本地 document 复验。"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Protocol

from niuniu_stock_announcer.storage.document import (
    resolve_storage_path,
    validate_storage_relative_path,
)

HASH_CHUNK_SIZE = 1024 * 1024


class VerifiableDocument(Protocol):
    """定义发送前必须复验的本地文件快照字段。"""

    storage_relative_path: str
    document_size_bytes: int
    document_sha256: str


@contextmanager
def open_verified_document(
    document: VerifiableDocument,
    *,
    storage_root: Path,
) -> Iterator[BinaryIO]:
    """用同一文件句柄复验并提供待发送 document。

    Args:
        document: outbox 中已冻结的相对路径、大小与 SHA-256。
        storage_root: 只允许相对路径落入的本地文档根目录。

    Yields:
        已回到文件开头、可直接交给 IM SDK 的二进制句柄。

    Raises:
        ValueError: 路径越界、目标不是普通文件或 size/hash 不一致。
        FileNotFoundError: 冻结路径对应的本地文件不存在。
    """
    normalized = validate_storage_relative_path(document.storage_relative_path)
    path = resolve_storage_path(storage_root, normalized)
    try:
        path_stat = path.stat()
        if not stat.S_ISREG(path_stat.st_mode):
            raise ValueError("本地 document 必须是普通文件")
        document_file = path.open("rb")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"本地 document 不存在: {normalized}") from exc
    except OSError as exc:
        raise ValueError(f"无法打开本地 document: {normalized}") from exc

    try:
        file_stat = os.fstat(document_file.fileno())
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("本地 document 必须是普通文件")
        if file_stat.st_size != document.document_size_bytes:
            raise ValueError("本地 document 大小与冻结 payload 不一致")
        digest = hashlib.sha256()
        while chunk := document_file.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
        if digest.hexdigest() != document.document_sha256:
            raise ValueError("本地 document SHA-256 与冻结 payload 不一致")
        # 校验和发送共用这个句柄，避免路径在两步之间被替换后发送未经验证的文件。
        document_file.seek(0)
        yield document_file
    finally:
        document_file.close()
