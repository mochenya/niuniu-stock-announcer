"""全仓库 pytest 安全门。"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@pytest.fixture(autouse=True)
def block_unmarked_external_network(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[None]:
    """阻断未标记测试的公网 DNS 和 socket 连接。

    Args:
        monkeypatch: pytest 提供的可恢复补丁工具。
        request: 当前测试节点，用于判断是否显式标记为 `live`。

    Yields:
        测试执行权；fixture 退出时自动恢复 socket 实现。
    """
    if request.node.get_closest_marker("live") is not None:
        yield
        return

    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo

    def guarded_connect(sock: socket.socket, address: object) -> object:
        if isinstance(address, tuple) and address:
            host = str(address[0]).strip("[]").lower()
            if host not in LOOPBACK_HOSTS:
                raise AssertionError(f"离线测试禁止外部网络连接: {host}")
        return original_connect(sock, address)

    def guarded_getaddrinfo(host: object, *args: object, **kwargs: object):
        normalized = str(host).strip("[]").lower()
        if normalized not in LOOPBACK_HOSTS:
            raise AssertionError(f"离线测试禁止外部 DNS 查询: {normalized}")
        return original_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    yield
