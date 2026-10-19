"""Debata_Agent 程序入口。

默认启动 **WebUI**（浏览器控制台，监听 0.0.0.0 供局域网访问）。
原 PySide6 桌面端已移除，`ui/` 目录与 `gui` 依赖不再存在。

启动顺序：
    1. 解析命令行参数
    2. 配置 logging（按 config.app.log_level）
    3. 安装高性能事件循环（Linux/Mac 上用 uvloop）
    4. 按模式分流：
         - 默认        → WebUI（webui/server.py）
         - --no-gui    → 纯命令行 Runtime（无任何界面）
         - --setup 等  → CLI 配置向导 / 专项维护命令

WebUI 首次启动时若发现没有 config.yaml，会在浏览器里给出配置向导；
纯命令行模式则会走 ``main_cli`` 的交互式向导。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from getpass import getpass
from logging.handlers import RotatingFileHandler
from pathlib import Path

import main_cli as _main_cli
from utils.proxy_env import sanitize_no_proxy_env

logger = logging.getLogger(__name__)

DEFAULT_WEBUI_HOST = "0.0.0.0"
DEFAULT_WEBUI_PORT = 8000

# 这些子命令交给 service_cli 处理，避免和正常启动参数混在一起。
_SERVICE_COMMANDS = frozenset(
    {
        "start",
        "stop",
        "restart",
        "status",
        "log",
        "enable",
        "disable",
        "install",
        "uninstall",
        "help",
    }
)


# ============================================================
# 日志与事件循环
# ============================================================


def setup_logging(
    level: str = "INFO",
    *,
    project_root: Path | None = None,
    logs_dir: Path | None = None,
) -> None:
    """配置标准 logging。

    格式：[YYYY-MM-DD HH:MM:SS] [LEVEL] [module] message
    """
    root = project_root or Path(__file__).resolve().parent
    logs_dir = logs_dir or root / "data" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file_handler = RotatingFileHandler(
        logs_dir / "debata.log",
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[console, file_handler],
        force=True,
    )
    for noisy_logger in ("websockets", "uvicorn.access"):
        logging.getLogger(noisy_logger).setLevel(logging.INFO)


def install_uvloop() -> None:
    """在 Linux/Mac 上安装 uvloop（如已安装）。Windows 跳过。"""
    if sys.platform == "win32":
        return
    try:
        import uvloop  # type: ignore

        uvloop.install()
        logging.getLogger(__name__).info("已启用 uvloop")
    except ImportError:
        pass


# ============================================================
# 参数
# ============================================================


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="debata",
        description="Debata_Agent —— 让虚拟角色活过来的通用框架（默认启动 WebUI）",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="纯命令行模式：只跑 Runtime，不启动 WebUI（服务器/容器里想完全无界面时用）",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_WEBUI_HOST,
        help=f"WebUI 监听地址（默认 {DEFAULT_WEBUI_HOST}，局域网可访问；只给本机用可填 127.0.0.1）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_WEBUI_PORT,
        help=f"WebUI 监听端口（默认 {DEFAULT_WEBUI_PORT}）",
    )
    parser.add_argument(
        "--token",
        default="",
        help="WebUI 访问令牌（默认自动生成并记住；想固定一个就填这里）",
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="关闭 WebUI 令牌鉴权。⚠ 能访问该端口的设备都能看聊天记录、改配置，仅限完全可信内网",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="",
        help="自定义配置文件路径（默认在 data/config.yaml）",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="强制运行 CLI 配置向导（即使已有 config.yaml）",
    )
    parser.add_argument(
        "--napcat",
        action="store_true",
        help="只重新配置 NapCat 适配器（mode/地址/端口/token），不动其它配置",
    )
    parser.add_argument(
        "--test-adapter",
        action="store_true",
        help="启动 NapCat adapter 5 秒，看能不能连上对端，然后退出",
    )
    parser.add_argument(
        "--list-secrets",
        action="store_true",
        help="列出 secrets.enc 中存储的所有密钥 ID（不显示值）",
    )
    return parser.parse_args(argv)


# ============================================================
# 纯命令行 Runtime
# ============================================================


async def run_headless(project_root: Path, config_file: Path | None = None) -> None:
    """无界面模式：直接跑 Runtime。"""
    from core import Runtime

    rt = Runtime(project_root=project_root, config_file=config_file)
    try:
        await rt.start()
        await rt.wait_until_stop()
    finally:
        await rt.shutdown()


# ============================================================
# main
# ============================================================


def main(argv: list[str] | None = None) -> None:
    """同步入口。"""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] in _SERVICE_COMMANDS:
        # 通过 .venv/bin/debata 启动时，sys.path[0] 是 .venv/bin；显式加入项目根，
        # 保证 service_cli 在源码运行和可编辑安装下都能导入。
        project_root_text = str(Path(__file__).resolve().parent)
        if project_root_text not in sys.path:
            sys.path.insert(0, project_root_text)
        from service_cli import main as service_main

        raise SystemExit(service_main(raw_argv))
    args = parse_args(raw_argv)
    project_root = Path(__file__).resolve().parent

    from app_config import AppPaths, initialize_runtime_data

    config_file = Path(args.config) if args.config else None
    paths = AppPaths(project_root=project_root, config_file=config_file)
    paths.ensure_data_dirs()
    initialize_runtime_data(paths, project_root=project_root)
    setup_logging("INFO", project_root=project_root, logs_dir=paths.LOGS_DIR)
    install_uvloop()

    # 清掉会让 httpx 客户端一构造就抛 InvalidURL 的 NO_PROXY 条目（如 [::1]）。
    # --no-gui 走 headless Runtime，不经过 webui.server.run_webui，所以这里也要做。
    sanitize_no_proxy_env()

    # ---- 专项维护命令：做完就退，不启服务 ----
    if args.napcat:
        _run_napcat_setup(paths)
        return

    if args.test_adapter:
        asyncio.run(_test_adapter(project_root, config_file=config_file))
        return

    if args.list_secrets:
        _run_list_secrets(paths)
        return

    if args.setup:
        _run_cli_wizard(paths)
        return

    # ---- 纯命令行模式 ----
    if args.no_gui:
        if not paths.CONFIG_FILE.exists():
            _run_cli_wizard(paths)
            return
        try:
            asyncio.run(run_headless(project_root, config_file=config_file))
        except KeyboardInterrupt:
            print("\n收到 Ctrl+C，已退出。")
        return

    # ---- 默认：WebUI ----
    _run_webui(
        project_root,
        host=args.host,
        port=args.port,
        token=args.token or None,
        no_auth=args.no_auth,
        config_file=config_file,
    )


def _run_webui(
    project_root: Path,
    *,
    host: str,
    port: int,
    token: str | None,
    no_auth: bool,
    config_file: Path | None,
) -> None:
    """启动 WebUI。没有 config.yaml 也不拦，浏览器里会给首次配置向导。"""
    from webui.server import run_webui

    run_webui(
        project_root,
        host=host,
        port=port,
        config_file=config_file,
        token=token,
        no_auth=no_auth,
    )


# ============================================================
# main_cli 桥接（保留原有 CLI 能力，去掉 Qt 相关部分）
# ============================================================


def _sync_cli_io() -> None:
    _main_cli.getpass = getpass


def _run_cli_wizard(paths) -> None:
    _sync_cli_io()
    _main_cli._run_cli_wizard(paths)


def _run_list_secrets(paths) -> None:
    _main_cli._run_list_secrets(paths)


def _run_napcat_setup(paths) -> None:
    _sync_cli_io()
    _main_cli._run_napcat_setup(paths)


# ============================================================
# --test-adapter：启动 5 秒看连接情况
# ============================================================


async def _test_adapter(project_root: Path, config_file: Path | None = None) -> None:
    """启动 NapCat adapter 5 秒，报告连接情况。"""
    from core import Runtime

    print("=" * 60)
    print("测试 NapCat 适配器连接（5 秒）")
    print("=" * 60)

    rt = Runtime(project_root=project_root, config_file=config_file)
    try:
        await rt.start()
    except Exception as e:
        print(f"\n✗ Runtime 启动失败：{e}")
        return

    # 启动后立刻打印配置摘要，方便用户判断"程序在用什么配置"
    adapter_name, cfg = next(iter(rt.config.adapters.items()))
    endpoint = f"ws://{cfg.host}:{cfg.port}{cfg.path}"
    print("\n程序配置：")
    print(f"  adapter  = {adapter_name}")
    print(f"  mode     = {cfg.mode}")
    print(f"  endpoint = {endpoint}")
    print(f"  token_id = {cfg.access_token_id or '(无)'}")
    if cfg.mode == "client":
        print(f"  NapCat 这边应该: 「正向 WS」监听 {endpoint}")
    else:
        print(f"  程序监听地址: {endpoint}")
        print("  NapCat 这边应该: 「反向 WS」目标填这台机器的局域网 IP + 同端口/路径")

    print("\nAdapter 已启动。等 5 秒看连接情况...")
    for i in range(5):
        await asyncio.sleep(1)
        connected = rt.adapter.is_connected
        marker = "✓" if connected else "·"
        print(f"  [{i + 1}/5] {marker} is_connected={connected}")
        if connected:
            print("\n✓ 连接成功！")
            break
    else:
        print("\n✗ 5 秒内未建立连接。检查：")
        if cfg.mode == "client":
            print(f"   - NapCat 那边「正向 WS」是否在 {endpoint} 监听？")
            print("   - Token 是否一致？")
        else:
            print("   - NapCat 那边「反向 WS」目标是否填了这台机器的局域网 IP？")
            print(f"   - 端口和路径是否与程序监听一致：{cfg.port}{cfg.path}？")
            print("   - Token 是否一致？")

    await rt.shutdown()
    print("=" * 60)


if __name__ == "__main__":
    main()
