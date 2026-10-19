"""WebUI 令牌鉴权。

WebUI 能读到全部聊天记录、改配置、换 API Key，所以默认**必须**带令牌才能访问，
即使在局域网里也一样。三种提供方式，优先级从高到低：

    1. 命令行 ``--token xxx``
    2. 环境变量 ``DEBATA_WEBUI_TOKEN``
    3. 数据目录下持久化的 ``webui_token.txt``（首次启动自动生成，权限 0600）

浏览器侧用 ``POST /api/auth/login`` 换取一个 HttpOnly 会话 cookie；
WebSocket 握手也认这个 cookie，另支持 ``?token=`` 查询参数（给脚本 / curl 用）。

``--no-auth`` 可以整体关掉鉴权，此时启动横幅会明确警告。
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import stat
import time
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_FILENAME = "webui_token.txt"
COOKIE_NAME = "debata_webui_session"
ENV_VAR = "DEBATA_WEBUI_TOKEN"

# 会话有效期（秒）。局域网自用场景给足 30 天，避免手机上反复登录。
SESSION_TTL_SECONDS = 30 * 24 * 3600


def _generate_token() -> str:
    return secrets.token_urlsafe(24)


def load_or_create_token(paths) -> tuple[str, str]:
    """返回 ``(token, 来源说明)``。

    优先命令行 / 环境变量；都没有就读数据目录里的 token 文件，文件不存在则生成。
    """
    env_token = os.environ.get(ENV_VAR, "").strip()
    if env_token:
        return env_token, f"环境变量 {ENV_VAR}"

    token_file = Path(paths.DATA_DIR) / TOKEN_FILENAME
    if token_file.exists():
        try:
            existing = token_file.read_text(encoding="utf-8").strip()
        except OSError as e:  # noqa: BLE001
            logger.warning("读取 %s 失败：%s，将重新生成", token_file, e)
        else:
            if existing:
                return existing, str(token_file)

    token = _generate_token()
    try:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token + "\n", encoding="utf-8")
        # 令牌等价于密码，收窄到仅本人可读。
        token_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as e:  # noqa: BLE001
        logger.warning("写入 %s 失败：%s（令牌仅在本次启动有效）", token_file, e)
        return token, "本次启动随机生成（未能落盘）"
    return token, str(token_file)


class TokenAuth:
    """令牌校验 + 会话 cookie 管理。"""

    def __init__(self, token: str | None, *, enabled: bool = True, source: str = "") -> None:
        self.enabled = enabled and bool(token)
        self.token = token or ""
        self.source = source
        self._sessions: dict[str, float] = {}

    # ----- 会话 -----

    def new_session(self) -> str:
        self._purge_expired()
        sid = secrets.token_urlsafe(32)
        self._sessions[sid] = time.time() + SESSION_TTL_SECONDS
        return sid

    def drop_session(self, sid: str | None) -> None:
        if sid:
            self._sessions.pop(sid, None)

    def session_valid(self, sid: str | None) -> bool:
        if not self.enabled:
            return True
        if not sid:
            return False
        expires = self._sessions.get(sid)
        if expires is None:
            return False
        if expires < time.time():
            self._sessions.pop(sid, None)
            return False
        return True

    def _purge_expired(self) -> None:
        now = time.time()
        for sid in [s for s, exp in self._sessions.items() if exp < now]:
            self._sessions.pop(sid, None)

    # ----- 令牌 -----

    def token_matches(self, candidate: str | None) -> bool:
        if not self.enabled:
            return True
        if not candidate:
            return False
        return hmac.compare_digest(candidate, self.token)

    def request_authorized(self, request) -> bool:
        """HTTP 请求是否已授权：会话 cookie 或 Bearer / 查询参数令牌。"""
        if not self.enabled:
            return True
        if self.session_valid(request.cookies.get(COOKIE_NAME)):
            return True
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            if self.token_matches(header[7:].strip()):
                return True
        return self.token_matches(request.query_params.get("token"))

    def websocket_authorized(self, websocket) -> bool:
        if not self.enabled:
            return True
        if self.session_valid(websocket.cookies.get(COOKIE_NAME)):
            return True
        return self.token_matches(websocket.query_params.get("token"))


# ============================================================
# 启动横幅
# ============================================================


def format_token_banner(auth: TokenAuth, *, host: str, port: int, urls: list[str]) -> str:
    """打印启动信息，令牌只在这里露出一次。"""
    lines = [
        "",
        "=" * 64,
        "  Debata_Agent WebUI 已启动",
        "=" * 64,
        f"  监听        : {host}:{port}",
    ]
    for url in urls:
        lines.append(f"  访问地址    : {url}")
    if auth.enabled:
        lines += [
            f"  访问令牌    : {auth.token}",
            f"  令牌来源    : {auth.source}",
            "  （浏览器首次打开会要求输入令牌，登录后 30 天内免输）",
        ]
    else:
        lines += [
            "  ⚠ 鉴权已关闭：能访问该端口的设备都能查看聊天记录并修改配置。",
            "    仅在完全可信的内网中使用。",
        ]
    lines += ["=" * 64, ""]
    return "\n".join(lines)


def lan_urls(host: str, port: int) -> list[str]:
    """列出可用的访问地址（含局域网 IP）。"""
    urls = [f"http://127.0.0.1:{port}"]
    if host in {"0.0.0.0", "::"}:
        for ip in _local_ips():
            urls.append(f"http://{ip}:{port}")
    elif host not in {"127.0.0.1", "localhost"}:
        urls.append(f"http://{host}:{port}")
    return urls


def _local_ips() -> list[str]:
    """尽力枚举本机局域网 IPv4，不依赖第三方包。"""
    ips: list[str] = []
    try:
        import socket

        # 连一个外网地址只为让内核挑出默认出口网卡，不会真的发包。
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.2)
            try:
                s.connect(("8.8.8.8", 80))
                ips.append(s.getsockname()[0])
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass

    try:
        import socket

        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:  # noqa: BLE001
        pass
    return ips


__all__ = [
    "COOKIE_NAME",
    "ENV_VAR",
    "SESSION_TTL_SECONDS",
    "TOKEN_FILENAME",
    "TokenAuth",
    "format_token_banner",
    "lan_urls",
    "load_or_create_token",
]
