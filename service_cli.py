"""Debata 后台服务管理命令。

用法::

    debata start       启动
    debata stop        停止
    debata restart     重启
    debata status      查看状态
    debata log         实时查看日志
    debata enable      设置开机自启（并立即启动）
    debata disable     取消开机自启
    debata install     只安装服务，不启动
    debata uninstall   卸载服务
    debata help        查看帮助

优先使用 systemd 用户服务。如果当前机器没有可用的 systemd 用户会话
（容器、WSL、精简系统等），自动回退到 PID 文件 + 独立后台进程模式，
``start/stop/restart/status/log`` 仍然可用；开机自启需要 systemd 或
容器/系统自带的启动机制。
"""

from __future__ import annotations

import getpass
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SERVICE_NAME = "debata-agent"
PROJECT_ROOT = Path(__file__).resolve().parent
UNIT_DIR = Path(
    os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
) / "systemd" / "user"
UNIT_FILE = UNIT_DIR / f"{SERVICE_NAME}.service"
INSTALLER = PROJECT_ROOT / "scripts" / "install_systemd_user.sh"
LAUNCHER = Path.home() / ".local" / "bin" / "debata"
LAUNCHER_MARKER = "# Debata Agent launcher"

# fallback 模式的运行状态放在项目内，避免 DEBATA_DATA_ROOT 不同导致找不到进程。
RUN_DIR = PROJECT_ROOT / ".debata-run"
PID_FILE = RUN_DIR / "debata.pid"
STATE_FILE = RUN_DIR / "debata.json"
SERVICE_ARGS_FILE = RUN_DIR / "args.txt"
CONSOLE_LOG = RUN_DIR / "console.log"

COMMANDS = (
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
)

HELP = """Debata 后台服务管理

用法:
  debata start       启动（首次会自动安装服务）
  debata stop        停止
  debata restart     重启
  debata status      查看状态
  debata log         实时查看日志（Ctrl+C 退出）
  debata enable      设置开机自启（并立即启动）
  debata disable     取消开机自启
  debata install     只安装服务，不启动
  debata uninstall   卸载服务
  debata help        显示本帮助

说明:
  - 有 systemd 用户会话时优先使用 systemd，不需要 sudo。
  - 没有 systemd 用户会话（容器 / WSL / 精简系统）时自动使用内置后台模式，
    start / stop / restart / status / log 仍然可用；
    开机自启需要容器重启策略或 cron @reboot。
  - systemd 模式下如果提示 linger 失败，执行: sudo loginctl enable-linger $USER
  - 自定义端口 / headless:
      bash scripts/install_systemd_user.sh --port 9000
      bash scripts/install_systemd_user.sh --headless
  - 完整文档: docs/background_running.md
"""


def _systemctl(*args: str) -> list[str]:
    # systemctl 的语法是 `systemctl [OPTIONS] COMMAND [UNIT]`，
    # 服务名必须放在命令之后，否则会把服务名当成未知命令。
    return ["systemctl", "--user", *args, SERVICE_NAME]


def _run(command: list[str]) -> int:
    try:
        return subprocess.call(command)
    except FileNotFoundError:
        print(f"命令不存在: {command[0]}")
        return 127
    except KeyboardInterrupt:
        return 130


def _systemd_available() -> bool:
    """检测 systemd 用户 bus 是否真的可用。

    只检查 ``systemctl`` 是否存在不够：容器 / WSL / 精简系统里命令可能
    存在，但 ``systemctl --user`` 会报 “Failed to connect to bus”。
    """
    if os.environ.get("DEBATA_FORCE_NO_SYSTEMD") == "1":
        return False
    if shutil.which("systemctl") is None:
        return False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _require_unit() -> bool:
    if UNIT_FILE.exists():
        return True
    print("服务尚未安装。先运行: debata start")
    return False


def _ensure_installed() -> bool:
    """服务不存在时自动调用安装脚本，只写 unit，不启动。"""
    if not _systemd_available():
        return True
    if UNIT_FILE.exists():
        return True
    if not INSTALLER.exists():
        print(f"找不到安装脚本: {INSTALLER}")
        return False
    print("首次使用，正在安装 systemd 服务...")
    return_code = _run(["bash", str(INSTALLER), "--write-only"])
    if return_code != 0 or not UNIT_FILE.exists():
        print("服务安装失败。可手动运行: bash scripts/install_systemd_user.sh")
        return False
    return True


# ============================================================
# 内置后台模式（没有 systemd 用户会话时）
# ============================================================


def _service_python() -> Path:
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    return python if python.exists() else Path(sys.executable)


def _configured_service_args() -> list[str]:
    """读取安装脚本保存的参数；环境变量优先，方便临时覆盖。"""
    raw = os.environ.get("DEBATA_SERVICE_ARGS", "").strip()
    if raw:
        try:
            return shlex.split(raw)
        except ValueError as exc:
            print(f"DEBATA_SERVICE_ARGS 解析失败（{exc}），忽略该变量。")
    try:
        lines = SERVICE_ARGS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line for line in lines if line.strip()]


def _fallback_argv() -> list[str]:
    extras = _configured_service_args()
    if not extras:
        extras = ["--host", "0.0.0.0", "--port", "8000"]
    return [str(_service_python()), str(PROJECT_ROOT / "main.py"), *extras]


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _clear_state() -> None:
    for path in (PID_FILE, STATE_FILE):
        try:
            path.unlink()
        except OSError:
            pass


def _read_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    # Linux 下僵尸进程仍能通过 kill(pid, 0)；读 stat 的第三个字段排除。
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="ignore")
        if stat.split()[2] == "Z":
            return False
    except (OSError, IndexError):
        pass
    return True


def _pid_is_debata(pid: int) -> bool:
    """确认 PID 仍然属于这个项目，避免误杀复用了 PID 的其它进程。"""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True
    cmdline = raw.replace(b"\0", b" ").decode(errors="ignore")
    return "main.py" in cmdline and str(PROJECT_ROOT) in cmdline


def _running_fallback_pid() -> int | None:
    pid = _read_pid()
    if pid is not None and _process_alive(pid) and _pid_is_debata(pid):
        return pid
    if pid is not None:
        _clear_state()
    return None


def _tail_file(path: Path, lines: int = 20) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(content.splitlines()[-lines:])


def _fallback_start() -> int:
    pid = _running_fallback_pid()
    if pid is not None:
        print(f"debata 已在运行（PID {pid}）。")
        print("查看状态: debata status")
        return 0

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    argv = _fallback_argv()
    with CONSOLE_LOG.open("ab") as output:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        output.write(f"\n===== {stamp} start =====\n".encode())
        output.write((" ".join(shlex.quote(arg) for arg in argv) + "\n").encode())
        output.flush()
        try:
            process = subprocess.Popen(
                argv,
                cwd=PROJECT_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            print(f"启动失败: {exc}")
            return 1

    PID_FILE.write_text(f"{process.pid}\n", encoding="utf-8")
    STATE_FILE.write_text(
        json.dumps(
            {
                "pid": process.pid,
                "started_at": time.time(),
                "argv": argv,
                "app_log": str(_app_log_path()),
                "console_log": str(CONSOLE_LOG),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # 端口冲突等启动前错误会很快退出；给它一点时间暴露出来。
    time.sleep(0.5)
    if process.poll() is not None:
        _clear_state()
        print(f"启动失败（退出码 {process.returncode}）。最近日志：")
        tail = _tail_file(CONSOLE_LOG)
        if tail:
            print(tail)
        return 1

    print(f"已在后台启动（PID {process.pid}，无 systemd）。")
    print("日志: debata log")
    print(f"WebUI 令牌: {_token_path()}（首次启动后生成）")
    print("停止: debata stop")
    return 0


def _fallback_stop(quiet: bool = False) -> int:
    pid = _running_fallback_pid()
    if pid is None:
        _clear_state()
        if not quiet:
            print("debata 未在运行。")
        return 0

    def _signal(sig: int) -> bool:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return False
        except PermissionError as exc:
            print(f"停止失败：没有权限向 PID {pid} 发送信号（{exc}）。")
            return False
        return True

    if not _signal(signal.SIGINT):
        if _process_alive(pid):
            return 1
        _clear_state()
        return 0

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            break
        time.sleep(0.2)

    if _process_alive(pid):
        _signal(signal.SIGTERM)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if not _process_alive(pid):
                break
            time.sleep(0.2)

    if _process_alive(pid):
        _signal(signal.SIGKILL)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not _process_alive(pid):
                break
            time.sleep(0.1)

    if _process_alive(pid):
        print(f"停止失败：PID {pid} 仍未退出。")
        return 1

    _clear_state()
    if not quiet:
        print(f"已停止 debata（PID {pid}）。")
    return 0


def _fallback_status() -> int:
    pid = _running_fallback_pid()
    if pid is None:
        print("● debata 未运行（内置后台模式，无 systemd）")
        print("  启动: debata start")
        return 3

    state = _read_state()
    print("● debata 正在运行（内置后台模式，无 systemd）")
    print(f"  PID       : {pid}")
    started_at = state.get("started_at")
    if isinstance(started_at, (int, float)):
        print(f"  启动时间  : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started_at))}")
    print(f"  项目目录  : {PROJECT_ROOT}")
    print(f"  应用日志  : {state.get('app_log') or _app_log_path()}")
    print(f"  控制台日志: {state.get('console_log') or CONSOLE_LOG}")
    return 0


# ============================================================
# 路径
# ============================================================


def _data_root() -> Path:
    return Path(
        os.environ.get("DEBATA_DATA_ROOT")
        or (Path.home() / ".local" / "share" / "Debata_Agent")
    )


def _token_path() -> Path:
    return _data_root() / "instances" / "default" / "webui_token.txt"


def _app_log_path() -> Path:
    return _data_root() / "instances" / "default" / "logs" / "debata.log"


# ============================================================
# 命令
# ============================================================


def cmd_start() -> int:
    if not _systemd_available():
        return _fallback_start()
    if not _ensure_installed():
        return 1
    return_code = _run(_systemctl("start"))
    if return_code == 0:
        print("已启动 debata-agent。")
        print("日志: debata log")
        print(f"WebUI 令牌: {_token_path()}（首次启动后生成）")
    else:
        print("启动失败。运行 debata log 查看原因。")
    return return_code


def cmd_stop() -> int:
    if not _systemd_available():
        return _fallback_stop()
    if not _require_unit():
        return 1
    return_code = _run(_systemctl("stop"))
    if return_code == 0:
        print("已停止 debata-agent。")
    return return_code


def cmd_restart() -> int:
    if not _systemd_available():
        _fallback_stop(quiet=True)
        code = _fallback_start()
        if code == 0:
            print("已重启 debata（内置后台模式）。")
        return code
    if not _ensure_installed():
        return 1
    return_code = _run(_systemctl("restart"))
    if return_code == 0:
        print("已重启 debata-agent。")
    else:
        print("重启失败。运行 debata log 查看原因。")
    return return_code


def cmd_status() -> int:
    if not _systemd_available():
        return _fallback_status()
    if not _require_unit():
        return 1
    return _run(["systemctl", "--user", "status", SERVICE_NAME, "--no-pager"])


def _journal_command() -> list[str] | None:
    """选择能读到本服务日志的 journalctl 调用方式。

    某些发行版（如部分 Armbian 配置）不保存用户 journal，日志实际写在
    系统 journal 的 ``_SYSTEMD_USER_UNIT`` 字段里；此时不能只用
    ``journalctl --user``。
    """
    if shutil.which("journalctl") is None:
        return None

    user_cmd = ["journalctl", "--user", "-u", SERVICE_NAME]
    probe = subprocess.run(
        [*user_cmd, "-n", "0", "--no-pager"],
        capture_output=True,
        text=True,
    )
    if "No journal files were found" not in (probe.stdout + probe.stderr):
        return [*user_cmd, "-n", "200", "-f"]

    system_cmd = [
        "journalctl",
        f"_SYSTEMD_USER_UNIT={SERVICE_NAME}.service",
    ]
    probe = subprocess.run(
        [*system_cmd, "-n", "1", "--no-pager"],
        capture_output=True,
        text=True,
    )
    output = probe.stdout + probe.stderr
    if "No journal files were found" not in output and "-- No entries --" not in output:
        return [*system_cmd, "-n", "200", "-f"]
    return None


def cmd_log() -> int:
    if not _systemd_available():
        state = _read_state()
        app_log = Path(state.get("app_log") or _app_log_path())
        if app_log.exists():
            return _run(["tail", "-n", "200", "-F", str(app_log)])
        if CONSOLE_LOG.exists():
            print(f"应用日志尚未生成，改为查看 {CONSOLE_LOG}")
            return _run(["tail", "-n", "200", "-F", str(CONSOLE_LOG)])
        print(f"日志尚未生成：{app_log}")
        return 1

    if not _require_unit():
        return 1
    command = _journal_command()
    if command is not None:
        return _run(command)
    log_file = _app_log_path()
    if log_file.exists():
        print(f"journald 中未找到日志，改为查看 {log_file}")
        return _run(["tail", "-n", "200", "-F", str(log_file)])
    print(f"当前系统没有可用日志：既没有 journald 记录，也没有 {log_file}")
    return 1


def _enable_linger() -> bool:
    user = os.environ.get("USER") or getpass.getuser()
    if shutil.which("loginctl") is None:
        print(f"提示：请手动执行 sudo loginctl enable-linger {user}")
        return False
    result = subprocess.run(
        ["loginctl", "enable-linger", user],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("已开启 linger：注销 SSH 后服务仍会运行。")
        return True
    print(f"提示：请执行 sudo loginctl enable-linger {user}")
    print("      否则注销/重启后服务可能不会继续运行。")
    return False


def cmd_enable() -> int:
    if not _systemd_available():
        print("当前环境没有可用的 systemd 用户会话，无法设置开机自启。")
        print("请使用容器重启策略，或把 `debata start` 加入 cron @reboot。")
        print("现在先启动内置后台进程。")
        return _fallback_start()
    if not _ensure_installed():
        return 1
    return_code = _run(["systemctl", "--user", "enable", SERVICE_NAME])
    if return_code != 0:
        print("设置开机自启失败。")
        return return_code
    start_code = _run(_systemctl("start"))
    _enable_linger()
    print("已设置开机自启：debata-agent")
    if start_code != 0:
        print("当前启动失败；运行 debata log 查看原因。")
    return 0


def cmd_disable() -> int:
    if not _systemd_available():
        print("当前为内置后台模式，没有 systemd 开机自启需要取消。")
        print("停止进程用: debata stop")
        return 0
    if not UNIT_FILE.exists():
        print("服务尚未安装。")
        return 1
    return_code = _run(["systemctl", "--user", "disable", SERVICE_NAME])
    if return_code == 0:
        print("已取消开机自启。")
        print("当前进程仍在运行；停止用: debata stop")
    return return_code


def _install_launcher() -> None:
    if LAUNCHER.exists():
        try:
            content = LAUNCHER.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            content = ""
        if LAUNCHER_MARKER not in content:
            print(f"提示：{LAUNCHER} 已存在且不是 Debata 启动器，跳过安装。")
            return
    LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHER.write_text(
        "#!/usr/bin/env bash\n"
        f"{LAUNCHER_MARKER}\n"
        f'exec {shlex.quote(str(_service_python()))} '
        f'{shlex.quote(str(PROJECT_ROOT / "main.py"))} "$@"\n',
        encoding="utf-8",
    )
    LAUNCHER.chmod(0o755)
    print(f"已安装命令: {LAUNCHER}")


def cmd_install() -> int:
    if not _systemd_available():
        _install_launcher()
        print("当前环境没有可用的 systemd 用户会话，使用内置后台模式。")
        print("无需安装服务，直接运行: debata start")
        return 0
    if not _ensure_installed():
        return 1
    print(f"服务已安装: {UNIT_FILE}")
    print("启动: debata start")
    return 0


def _remove_launcher() -> None:
    if not LAUNCHER.exists():
        return
    try:
        content = LAUNCHER.read_text(encoding="utf-8", errors="ignore")
        if LAUNCHER_MARKER in content:
            LAUNCHER.unlink()
            print(f"已移除命令: {LAUNCHER}")
    except OSError:
        pass


def cmd_uninstall() -> int:
    if not _systemd_available():
        _fallback_stop(quiet=True)
        if UNIT_FILE.exists():
            try:
                UNIT_FILE.unlink()
                print("已删除残留的 systemd unit。")
            except OSError as exc:
                print(f"删除 unit 失败: {exc}")
                return 1
        _remove_launcher()
        print("已卸载 Debata 后台服务。")
        return 0

    if not UNIT_FILE.exists():
        print("服务未安装。")
    else:
        _run(["systemctl", "--user", "disable", "--now", SERVICE_NAME])
        try:
            UNIT_FILE.unlink()
        except OSError as exc:
            print(f"删除 unit 失败: {exc}")
            return 1
        _run(["systemctl", "--user", "daemon-reload"])
        print("已卸载 debata-agent 服务。")

    _remove_launcher()
    return 0


_DISPATCH = {
    "start": cmd_start,
    "stop": cmd_stop,
    "restart": cmd_restart,
    "status": cmd_status,
    "log": cmd_log,
    "enable": cmd_enable,
    "disable": cmd_disable,
    "install": cmd_install,
    "uninstall": cmd_uninstall,
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("help", "-h", "--help"):
        print(HELP)
        return 0
    command = args[0]
    handler = _DISPATCH.get(command)
    if handler is None:
        print(f"未知命令: {command}\n")
        print(HELP)
        return 2
    return handler()


__all__ = ["COMMANDS", "SERVICE_NAME", "main"]
