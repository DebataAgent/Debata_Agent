#!/usr/bin/env python3
"""Debata_Agent WebUI 启动脚本。

用法::

    uv run webui_server.py                 # 默认 0.0.0.0:8000，局域网可访问
    uv run webui_server.py --port 9000
    uv run webui_server.py --host 127.0.0.1   # 只允许本机
    uv run webui_server.py --no-auth          # 关掉令牌鉴权（仅限可信内网）

首次启动会自动生成访问令牌并打印在终端，同时存到数据目录的 webui_token.txt。
浏览器打开任意一个打印出来的地址，输入令牌即可进入控制台。

这个脚本只做参数解析，真正的服务在 ``webui/server.py``；``main.py`` 也复用它，
所以 `uv run main.py` 的行为与这里完全一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    from webui.server import main as webui_main

    return webui_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
