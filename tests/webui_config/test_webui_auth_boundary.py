"""WebUI 鉴权边界。

WebUI 能读到全部聊天记录、改配置、换 API Key，所以"哪些路径不需要令牌"
是一条安全属性，改动时必须有人拦一下。

背景：``webui/server.py`` 的中间件用 ``path.startswith(PUBLIC_PREFIXES)`` 放行，
前缀匹配很容易被"看起来像"的路径蹭过去（例如把上传目录挂成 ``/static/emoji``），
这里的用例就是盯住这一点。
"""

from __future__ import annotations

import re

import pytest

from webui.server import PUBLIC_PREFIXES


def _is_public(path: str) -> bool:
    return path == "/" or path.startswith(PUBLIC_PREFIXES)


def test_root_and_static_are_public():
    """登录页本身要能打开，否则没法输入令牌。"""
    assert _is_public("/")
    assert _is_public("/static/app.css")
    assert _is_public("/api/auth/login")
    assert _is_public("/api/auth/state")


@pytest.mark.parametrize(
    "path",
    [
        "/api/emoji",
        "/api/emoji/names",
        "/api/emoji/file/猫.png",
        "/api/emoji/upload",
        "/api/emoji/猫",
        "/api/chats/conversation",
        "/api/settings",
        "/api/settings/providers",
        "/api/settings/secrets",
        "/api/personas",
        "/api/logs",
        "/api/memory",
    ],
)
def test_sensitive_paths_require_token(path):
    assert not _is_public(path), f"{path} 不该免鉴权"


def test_emoji_files_are_not_static_mounted():
    """表情包目录绝不能挂成静态目录。

    一旦挂载，目录里任何非图片文件（用户手动丢进去的笔记、.env 之类）
    都会被原样下载，绕过 emoji_store 的扩展名与 magic 校验。
    出图只能走 /api/emoji/file/{filename}，那条路径带鉴权且逐项校验。
    """
    from pathlib import Path

    import webui.server as server

    source = Path(server.__file__).read_text(encoding="utf-8")
    mounts = re.findall(r"app\.mount\(\s*[\"']([^\"']+)", source)
    assert mounts, "没找到任何静态挂载，测试需要更新"
    assert mounts == ["/static"], f"新增了未预期的静态挂载：{mounts}"

    # server.py 不该知道 EMOJI_DIR 的存在——它只该出现在 api/emoji.py 与 paths.py 里
    assert "EMOJI_DIR" not in source
