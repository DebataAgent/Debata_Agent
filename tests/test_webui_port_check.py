"""WebUI 端口预检测试。"""

from __future__ import annotations

import socket

from webui.server import _port_available


def test_port_available_detects_busy_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        assert _port_available("127.0.0.1", port) is False
    finally:
        sock.close()

    assert _port_available("127.0.0.1", port) is True
