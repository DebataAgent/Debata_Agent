"""WebUI 服务装配与启动。

一个 uvicorn 进程同时承载两件事：
    1. ``core.Runtime`` —— 在 FastAPI lifespan 里启动，和 HTTP 服务共用同一个事件循环
    2. HTTP / WebSocket 接口 —— 各页面读写 Runtime 的状态

这样 WebUI 不需要 qasync 那套「Qt 事件循环 + asyncio 桥接」，也不存在跨线程
访问 asyncio 对象的问题。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from utils.proxy_env import sanitize_no_proxy_env

from .api import ALL_ROUTERS
from .auth import (
    TokenAuth,
    format_token_banner,
    lan_urls,
    load_or_create_token,
)
from .events import EventHub
from .logbuffer import install_log_buffer
from .runtime_host import RuntimeHost

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# 不需要鉴权的路径前缀（登录接口本身 + 前端静态资源）
PUBLIC_PREFIXES = ("/api/auth/", "/static/", "/favicon.ico", "/docs", "/openapi.json")


def create_app(host: RuntimeHost, auth: TokenAuth) -> FastAPI:
    """构造 FastAPI 应用。"""
    log_buffer = install_log_buffer()
    hub = EventHub(host, log_buffer)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        log_buffer.bind_loop(loop)
        hub.bind_loop(loop)
        await hub.start()

        # Runtime 启动可能要几秒（探测密钥、拉起插件），放后台跑，
        # 让 HTTP 服务先起来，前端能立刻显示「启动中」。
        boot_task = asyncio.create_task(_boot(host, hub), name="webui-boot-runtime")
        try:
            yield
        finally:
            boot_task.cancel()
            try:
                await boot_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            await hub.stop()
            await host.stop()

    app = FastAPI(
        title="Debata_Agent WebUI",
        description="Debata_Agent 的浏览器控制台（原 PySide6 桌面端的替代）",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.host = host
    app.state.auth = auth
    app.state.hub = hub
    app.state.log_buffer = log_buffer

    for router in ALL_ROUTERS:
        app.include_router(router)

    _install_config_error_handler(app)
    _install_auth_middleware(app, auth)
    _install_routes(app)
    return app


async def _boot(host: RuntimeHost, hub: EventHub) -> None:
    """后台启动 Runtime。"""
    try:
        ok = await host.start()
        if ok:
            hub.attach_timeline()
    except Exception:  # noqa: BLE001
        logger.exception("Runtime 后台启动失败")


def _install_config_error_handler(app: FastAPI) -> None:
    """配置写入被拒 → 400 带原文，而不是 500「Internal Server Error」。

    ``RuntimeHost.save_config`` 落盘前会校验，失败抛 ``ConfigError``；
    前端要把这段话原样弹给用户（说的是哪个字段不合法、该怎么改）。
    """
    from app_config import ConfigError

    @app.exception_handler(ConfigError)
    async def _handle(request: Request, exc: ConfigError) -> JSONResponse:
        logger.warning("配置操作被拒绝 %s: %s", request.url.path, exc)
        return JSONResponse(status_code=400, content={"detail": str(exc)})


def _install_auth_middleware(app: FastAPI, auth: TokenAuth) -> None:
    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if not auth.enabled or path == "/" or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)
        if auth.request_authorized(request):
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"detail": "未授权：请在页面上输入访问令牌", "auth_required": True},
        )


def _install_routes(app: FastAPI) -> None:
    @app.get("/", include_in_schema=False)
    async def index() -> Any:
        index_file = STATIC_DIR / "index.html"
        if not index_file.is_file():
            return JSONResponse(status_code=500, content={"detail": "前端资源缺失"})
        return FileResponse(index_file, media_type="text/html; charset=utf-8")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Any:
        icon = STATIC_DIR / "favicon.svg"
        if icon.is_file():
            return FileResponse(icon, media_type="image/svg+xml")
        return JSONResponse(status_code=404, content={})

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket) -> None:
        """一条连接同时收 log / chat / status 三类事件。"""
        auth: TokenAuth = websocket.app.state.auth
        if not auth.websocket_authorized(websocket):
            await websocket.close(code=4401, reason="unauthorized")
            return

        await websocket.accept()
        hub: EventHub = websocket.app.state.hub
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        hub.register(queue)
        pump = asyncio.create_task(hub.pump_logs(queue), name="webui-ws-log-pump")
        try:
            while True:
                payload = await queue.get()
                await websocket.send_json(payload)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            logger.debug("WebSocket 连接异常结束", exc_info=True)
        finally:
            pump.cancel()
            hub.unregister(queue)


# ============================================================
# 启动入口
# ============================================================


def _port_available(host: str, port: int) -> bool:
    """检查端口是否还能绑定。

    在构造 Runtime 之前先检查，避免端口被占用时先加载配置/拉起 Runtime，
    最后才由 uvicorn 抛出一大段 Errno 98 栈——那看起来像“启动崩溃”。
    """
    try:
        infos = socket.getaddrinfo(
            host or "0.0.0.0",
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        return True
    for family, socktype, proto, _, sockaddr in infos:
        sock = socket.socket(family, socktype, proto)
        try:
            if os.name != "nt":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(sockaddr)
        except OSError:
            return False
        finally:
            sock.close()
    return True


def run_webui(
    project_root: Path,
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    config_file: Path | None = None,
    token: str | None = None,
    no_auth: bool = False,
    log_level: str = "info",
) -> None:
    """启动 WebUI（阻塞直到 Ctrl+C）。"""
    import uvicorn

    if not _port_available(host, port):
        print(
            f"\n端口 {port} 已被占用，Debata 可能已经在运行。\n"
            f"  查看状态: debata status\n"
            f"  查看日志: debata log\n"
            f"  换端口启动: python main.py --host {host} --port 9000\n",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1)

    runtime_host = RuntimeHost(project_root=project_root, config_file=config_file)
    paths = runtime_host.build_paths()
    _ensure_logging(project_root, paths)

    # 必须早于任何 httpx 客户端构造（客户端要等 lifespan 里 host.start() 才建）：
    # NO_PROXY 里的 [::1] 会让 AsyncOpenAI 一构造就抛 InvalidURL，provider 建不出来，
    # Runtime 就停在启动失败。放在 _ensure_logging 之后，清理动作才有日志可看。
    sanitize_no_proxy_env()

    if no_auth:
        auth = TokenAuth(None, enabled=False, source="--no-auth")
    elif token:
        auth = TokenAuth(token, enabled=True, source="--token")
    else:
        value, source = load_or_create_token(paths)
        auth = TokenAuth(value, enabled=True, source=source)

    app = create_app(runtime_host, auth)

    # flush：输出被重定向到文件/容器日志时，stdout 是块缓冲的，
    # 不主动刷就会让「启动横幅 + 令牌」迟迟不出现。
    print(format_token_banner(auth, host=host, port=port, urls=lan_urls(host, port)), flush=True)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level,
        access_log=False,
        ws_ping_interval=25.0,
        ws_ping_timeout=25.0,
        # 不设置时 uvicorn 会无限等待 WebSocket 客户端自行断开，
        # NapCat / 浏览器保持连接时 `debata stop` 会被 systemd 超时强杀。
        timeout_graceful_shutdown=10,
    )
    server = uvicorn.Server(config)
    app.state.server = server

    try:
        server.run()
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，已退出。")


def _ensure_logging(project_root: Path, paths: Any) -> None:
    """确保 root logger 已配置。

    ``main.py`` 启动时会自己配好 logging，但 ``webui_server.py`` /
    ``python -m webui.server`` 是直接进来的，不配的话 Runtime 的报错
    只会落到 ``logging.lastResort``，用户看不到栈。
    """
    if logging.getLogger().handlers:
        return
    try:
        from main import setup_logging

        setup_logging("INFO", project_root=project_root, logs_dir=paths.LOGS_DIR)
    except Exception:  # noqa: BLE001
        logging.basicConfig(level=logging.INFO)


def main(argv: list[str] | None = None) -> int:
    """``python -m webui.server`` 的入口（也供根目录脚本复用）。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="debata-webui", description="Debata_Agent WebUI 服务"
    )
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0，局域网可访问）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument("--config", default="", help="自定义配置文件路径")
    parser.add_argument("--token", default="", help="访问令牌（默认自动生成并记住）")
    parser.add_argument("--no-auth", action="store_true", help="关闭令牌鉴权（仅限完全可信内网）")
    parser.add_argument("--log-level", default="info", help="uvicorn 日志级别")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parent.parent
    config_file = Path(args.config) if args.config else None

    run_webui(
        project_root,
        host=args.host,
        port=args.port,
        config_file=config_file,
        token=args.token or None,
        no_auth=args.no_auth,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
