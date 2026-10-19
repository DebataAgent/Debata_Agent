#!/usr/bin/env bash
# 安装 Debata Agent 的 systemd 用户服务（无需 sudo）。
#
# 用法：
#   bash scripts/install_systemd_user.sh
#   bash scripts/install_systemd_user.sh --headless          # 不启动 WebUI
#   bash scripts/install_systemd_user.sh --host 127.0.0.1 --port 9000
#   bash scripts/install_systemd_user.sh --no-auth           # 仅完全可信内网
#   bash scripts/install_systemd_user.sh --dry-run           # 只打印 unit
#
# 卸载：
#   systemctl --user disable --now debata-agent
#   rm -f ~/.config/systemd/user/debata-agent.service
#   systemctl --user daemon-reload

set -euo pipefail

SERVICE_NAME="debata-agent"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
TARGET_UNIT_FILE="$UNIT_DIR/$SERVICE_NAME.service"
UNIT_FILE="$TARGET_UNIT_FILE"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
HEADLESS=0
DRY_RUN=0
WRITE_ONLY=0
HOST="0.0.0.0"
PORT="8000"
EXTRA_ARGS=()

usage() {
    cat <<'EOF'
用法: bash scripts/install_systemd_user.sh [选项]

  --headless       只跑 headless Runtime（不启动 WebUI）
  --host HOST      WebUI 监听地址（默认 0.0.0.0）
  --port PORT      WebUI 端口（默认 8000）
  --no-auth        关闭 WebUI 鉴权（仅完全可信内网）
  --dry-run        只打印生成的 unit，不写入、不启动
  --write-only     写入 unit 和 debata 命令，但不启动服务
  -h, --help       显示帮助
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --headless)
            HEADLESS=1
            shift
            ;;
        --host)
            HOST="${2:-}"
            shift 2
            ;;
        --port)
            PORT="${2:-}"
            shift 2
            ;;
        --no-auth)
            EXTRA_ARGS+=("--no-auth")
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --write-only)
            WRITE_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
    echo "端口不合法: $PORT（应为 1-65535）" >&2
    exit 2
fi

if [[ ! -f "$PROJECT_ROOT/main.py" ]]; then
    echo "找不到 $PROJECT_ROOT/main.py，请在 Debata_Agent 项目内运行。" >&2
    exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
    cat >&2 <<EOF
找不到可执行的 $PYTHON。
请先安装依赖：
    cd "$PROJECT_ROOT"
    uv sync
EOF
    exit 1
fi

# 只有 systemctl 存在还不够：容器 / WSL / 精简系统里通常没有用户 bus。
HAS_SYSTEMD_USER=0
if [[ "${DEBATA_FORCE_NO_SYSTEMD:-0}" != "1" ]] && command -v systemctl >/dev/null 2>&1; then
    if systemctl --user show-environment >/dev/null 2>&1; then
        HAS_SYSTEMD_USER=1
    fi
fi

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    echo "警告：当前以 root 运行。建议用普通用户安装用户服务，或改用系统级模板。" >&2
fi

EXEC_ARGS=()
if [[ "$HEADLESS" -eq 1 ]]; then
    EXEC_ARGS+=("--no-gui")
else
    EXEC_ARGS+=("--host" "$HOST" "--port" "$PORT")
fi
EXEC_ARGS+=("${EXTRA_ARGS[@]}")

if [[ "$DRY_RUN" -eq 1 ]]; then
    UNIT_FILE="$(mktemp)"
elif [[ "$HAS_SYSTEMD_USER" -eq 1 ]]; then
    mkdir -p "$UNIT_DIR"
fi
QUOTED_EXTRA=""
for arg in "${EXEC_ARGS[@]}"; do
    QUOTED_EXTRA+=" $(printf '%q' "$arg")"
done

if [[ "$DRY_RUN" -eq 1 || "$HAS_SYSTEMD_USER" -eq 1 ]]; then
    cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Debata Agent ($([[ $HEADLESS -eq 1 ]] && echo "headless" || echo "WebUI + runtime"))
Documentation=file://$PROJECT_ROOT/docs/background_running.md
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory=$PROJECT_ROOT
ExecStart="$PYTHON" "$PROJECT_ROOT/main.py"$QUOTED_EXTRA
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-$HOME/.config/debata-agent.env
Restart=on-failure
RestartSec=5
TimeoutStopSec=60
KillSignal=SIGINT
UMask=0077
NoNewPrivileges=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
    chmod 0644 "$UNIT_FILE"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "# 将写入: $TARGET_UNIT_FILE"
    cat "$UNIT_FILE"
    rm -f "$UNIT_FILE"
    exit 0
fi

if [[ "$HAS_SYSTEMD_USER" -eq 1 ]]; then
    echo "已写入: $TARGET_UNIT_FILE"
else
    echo "未检测到可用的 systemd 用户会话，跳过 systemd unit 安装。"
fi

# 安装 debata 管理命令（不覆盖已有的非 Debata 命令）
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/debata"
if [[ -e "$LAUNCHER" ]] && ! grep -q "Debata Agent launcher" "$LAUNCHER" 2>/dev/null; then
    echo "提示：$LAUNCHER 已存在且不是 Debata 启动器，跳过安装 debata 命令。"
else
    mkdir -p "$BIN_DIR"
    cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Debata Agent launcher
exec "$PYTHON" "$PROJECT_ROOT/main.py" "\$@"
EOF
    chmod +x "$LAUNCHER"
    echo "已安装命令: $LAUNCHER"
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) echo "提示：$BIN_DIR 不在 PATH；可以直接用 ./debata，或把它加入 PATH。" ;;
    esac
fi

# 保存安装参数，systemd 不可用时 `debata start` 会沿用相同配置。
mkdir -p "$PROJECT_ROOT/.debata-run"
printf '%s\n' "${EXEC_ARGS[@]}" > "$PROJECT_ROOT/.debata-run/args.txt"

if [[ "$HAS_SYSTEMD_USER" -eq 1 ]]; then
    systemctl --user daemon-reload

    if [[ "$WRITE_ONLY" -eq 1 ]]; then
        echo "服务已写入，未启动。运行: debata start"
        exit 0
    fi

    systemctl --user enable --now "$SERVICE_NAME"

    LINGER="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"
    if [[ "$LINGER" != "yes" ]]; then
        cat <<EOF

提示：当前没有开启 linger。SSH 注销/重启后服务可能不会继续运行。
建议执行（需要 sudo）：
    sudo loginctl enable-linger "$USER"
EOF
    fi

    echo
    systemctl --user --no-pager status "$SERVICE_NAME" || true
else
    echo
    cat <<EOF
未检测到可用的 systemd 用户会话（systemctl --user 无法连接）。
已启用内置后台模式：start / stop / restart / status / log 可直接使用。
开机自启无法通过 systemd 设置，请使用容器重启策略或 cron @reboot。
EOF

    if [[ "$WRITE_ONLY" -eq 1 ]]; then
        echo "运行: debata start"
        exit 0
    fi

    echo
    "$PYTHON" "$PROJECT_ROOT/main.py" start
    echo
    "$PYTHON" "$PROJECT_ROOT/main.py" status || true
fi

echo
echo "常用命令："
echo "  启动     : debata start"
echo "  停止     : debata stop"
echo "  重启     : debata restart"
echo "  状态     : debata status"
echo "  日志     : debata log"
if [[ "$HAS_SYSTEMD_USER" -eq 1 ]]; then
    echo "  开机自启 : debata enable"
    echo "  取消自启 : debata disable"
else
    echo "  开机自启 : 不可用（请使用容器重启策略或 cron @reboot）"
fi
echo "  （未加入 PATH 时用 ./debata start）"
if [[ "$HEADLESS" -eq 0 ]]; then
    echo "  WebUI   : http://127.0.0.1:$PORT"
    echo "  令牌文件: ~/.local/share/Debata_Agent/instances/default/webui_token.txt"
    if [[ "$HAS_SYSTEMD_USER" -eq 1 ]]; then
        echo "            （首次启动后生成；也可用 journalctl 查看启动横幅）"
    else
        echo "            （首次启动后生成；日志用 debata log 查看）"
    fi
fi
