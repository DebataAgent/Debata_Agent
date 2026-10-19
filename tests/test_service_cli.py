"""简化服务命令测试（不触碰真实 systemd）。"""

from __future__ import annotations

import json
import signal
from types import SimpleNamespace

import service_cli


def test_help_lists_short_commands(capsys):
    assert service_cli.main(["help"]) == 0
    output = capsys.readouterr().out
    for command in ("start", "stop", "restart", "status", "log", "enable", "disable"):
        assert f"debata {command}" in output


def test_unknown_command_returns_2(capsys):
    assert service_cli.main(["nope"]) == 2
    assert "未知命令" in capsys.readouterr().out


def test_systemd_available_requires_user_bus(monkeypatch):
    monkeypatch.delenv("DEBATA_FORCE_NO_SYSTEMD", raising=False)
    monkeypatch.setattr(service_cli.shutil, "which", lambda name: None)
    assert service_cli._systemd_available() is False

    monkeypatch.setattr(
        service_cli.shutil, "which", lambda name: f"/usr/bin/{name}"
    )
    monkeypatch.setattr(
        service_cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="bus error"),
    )
    assert service_cli._systemd_available() is False

    monkeypatch.setattr(
        service_cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert service_cli._systemd_available() is True

    monkeypatch.setenv("DEBATA_FORCE_NO_SYSTEMD", "1")
    assert service_cli._systemd_available() is False


def test_start_calls_systemctl_start(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(service_cli, "_systemd_available", lambda: True)
    monkeypatch.setattr(service_cli, "_ensure_installed", lambda: True)
    monkeypatch.setattr(
        service_cli,
        "_run",
        lambda command: calls.append(command) or 0,
    )

    assert service_cli.main(["start"]) == 0
    assert calls == [["systemctl", "--user", "start", "debata-agent"]]


def test_start_uses_fallback_without_systemd(monkeypatch):
    monkeypatch.setattr(service_cli, "_systemd_available", lambda: False)
    monkeypatch.setattr(service_cli, "_fallback_start", lambda: 0)

    assert service_cli.main(["start"]) == 0


def test_enable_runs_enable_start_and_linger(monkeypatch):
    calls: list[list[str]] = []
    linger_calls: list[list[str]] = []
    monkeypatch.setattr(service_cli, "_systemd_available", lambda: True)
    monkeypatch.setattr(service_cli, "_ensure_installed", lambda: True)
    monkeypatch.setattr(
        service_cli,
        "_run",
        lambda command: calls.append(command) or 0,
    )
    monkeypatch.setattr(
        service_cli.shutil,
        "which",
        lambda name: f"/usr/bin/{name}",
    )

    def fake_run(command, **_kwargs):
        linger_calls.append(list(command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(service_cli.subprocess, "run", fake_run)

    assert service_cli.main(["enable"]) == 0
    assert ["systemctl", "--user", "enable", "debata-agent"] in calls
    assert ["systemctl", "--user", "start", "debata-agent"] in calls
    assert linger_calls
    assert linger_calls[0][:2] == ["loginctl", "enable-linger"]


def test_enable_starts_fallback_and_explains_autostart(monkeypatch, capsys):
    monkeypatch.setattr(service_cli, "_systemd_available", lambda: False)
    monkeypatch.setattr(service_cli, "_fallback_start", lambda: 0)

    assert service_cli.main(["enable"]) == 0
    output = capsys.readouterr().out
    assert "无法设置开机自启" in output
    assert "现在先启动内置后台进程" in output


def test_disable_only_disables_autostart(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    unit_file = tmp_path / "debata-agent.service"
    unit_file.write_text("[Unit]\n", encoding="utf-8")
    monkeypatch.setattr(service_cli, "UNIT_FILE", unit_file)
    monkeypatch.setattr(service_cli, "_systemd_available", lambda: True)
    monkeypatch.setattr(
        service_cli,
        "_run",
        lambda command: calls.append(command) or 0,
    )

    assert service_cli.main(["disable"]) == 0
    assert calls == [["systemctl", "--user", "disable", "debata-agent"]]


def test_disable_is_noop_without_systemd(monkeypatch, capsys):
    monkeypatch.setattr(service_cli, "_systemd_available", lambda: False)

    assert service_cli.main(["disable"]) == 0
    assert "没有 systemd 开机自启" in capsys.readouterr().out


def test_log_uses_system_journal_when_user_journal_is_missing(monkeypatch):
    monkeypatch.setattr(
        service_cli.shutil, "which", lambda name: f"/usr/bin/{name}"
    )

    def fake_run(command, **_kwargs):
        if "--user" in command:
            return SimpleNamespace(
                returncode=0,
                stdout="",
                stderr="No journal files were found.\n",
            )
        return SimpleNamespace(
            returncode=0,
            stdout="Oct 20 00:00:00 host python[1]: hello\n",
            stderr="",
        )

    monkeypatch.setattr(service_cli.subprocess, "run", fake_run)

    command = service_cli._journal_command()

    assert command is not None
    assert "_SYSTEMD_USER_UNIT=debata-agent.service" in command
    assert command[-1] == "-f"


def test_log_falls_back_to_app_log_file(monkeypatch, tmp_path, capsys):
    log_file = tmp_path / "debata.log"
    log_file.write_text("hello\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(service_cli, "UNIT_FILE", tmp_path / "unit")
    (tmp_path / "unit").write_text("[Unit]\n", encoding="utf-8")
    monkeypatch.setattr(service_cli, "_systemd_available", lambda: True)
    monkeypatch.setattr(service_cli, "_journal_command", lambda: None)
    monkeypatch.setattr(service_cli, "_app_log_path", lambda: log_file)
    monkeypatch.setattr(
        service_cli,
        "_run",
        lambda command: calls.append(command) or 0,
    )

    assert service_cli.main(["log"]) == 0
    assert calls == [["tail", "-n", "200", "-F", str(log_file)]]
    assert "debata.log" in capsys.readouterr().out


def _patch_fallback_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(service_cli, "RUN_DIR", tmp_path)
    monkeypatch.setattr(service_cli, "PID_FILE", tmp_path / "debata.pid")
    monkeypatch.setattr(service_cli, "STATE_FILE", tmp_path / "debata.json")
    monkeypatch.setattr(
        service_cli, "SERVICE_ARGS_FILE", tmp_path / "args.txt"
    )
    monkeypatch.setattr(service_cli, "CONSOLE_LOG", tmp_path / "console.log")
    monkeypatch.setattr(service_cli.time, "sleep", lambda _seconds: None)


def test_fallback_start_writes_pid_and_state(monkeypatch, tmp_path):
    _patch_fallback_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(service_cli, "_running_fallback_pid", lambda: None)
    monkeypatch.setattr(
        service_cli,
        "_fallback_argv",
        lambda: ["python", "main.py", "--host", "127.0.0.1", "--port", "8010"],
    )
    monkeypatch.setattr(service_cli, "_token_path", lambda: tmp_path / "token")

    class FakeProcess:
        pid = 4321
        returncode = None

        def poll(self):
            return None

    monkeypatch.setattr(
        service_cli.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess()
    )

    assert service_cli._fallback_start() == 0
    assert (tmp_path / "debata.pid").read_text(encoding="utf-8").strip() == "4321"
    state = json.loads((tmp_path / "debata.json").read_text(encoding="utf-8"))
    assert state["pid"] == 4321
    assert state["argv"][-2:] == ["--port", "8010"]


def test_fallback_stop_sends_sigint_and_clears_state(monkeypatch, tmp_path):
    _patch_fallback_paths(monkeypatch, tmp_path)
    (tmp_path / "debata.pid").write_text("4321\n", encoding="utf-8")
    (tmp_path / "debata.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(service_cli, "_running_fallback_pid", lambda: 4321)

    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        service_cli.os,
        "kill",
        lambda pid, sig: signals.append((pid, sig)),
    )
    alive = iter([True, False])
    monkeypatch.setattr(
        service_cli, "_process_alive", lambda _pid: next(alive, False)
    )

    assert service_cli._fallback_stop() == 0
    assert signals == [(4321, signal.SIGINT)]
    assert not (tmp_path / "debata.pid").exists()
    assert not (tmp_path / "debata.json").exists()


def test_fallback_status_when_not_running(monkeypatch, capsys):
    monkeypatch.setattr(service_cli, "_running_fallback_pid", lambda: None)

    assert service_cli._fallback_status() == 3
    assert "未运行" in capsys.readouterr().out


def test_fallback_reads_saved_service_args(monkeypatch, tmp_path):
    _patch_fallback_paths(monkeypatch, tmp_path)
    args_file = tmp_path / "args.txt"
    args_file.write_text(
        "--host\n127.0.0.1\n--port\n8010\n--no-gui\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DEBATA_SERVICE_ARGS", raising=False)

    assert service_cli._configured_service_args() == [
        "--host",
        "127.0.0.1",
        "--port",
        "8010",
        "--no-gui",
    ]
    assert service_cli._fallback_argv()[-5:] == [
        "--host",
        "127.0.0.1",
        "--port",
        "8010",
        "--no-gui",
    ]

    monkeypatch.setenv("DEBATA_SERVICE_ARGS", "--port 8020")
    assert service_cli._configured_service_args() == ["--port", "8020"]
