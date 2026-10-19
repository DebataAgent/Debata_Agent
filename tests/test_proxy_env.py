"""``NO_PROXY`` 清理的回归测试。

对应故障：``NO_PROXY`` 里出现**带方括号**的 IPv6（``[::1]``，Cherry Studio 等
运行时会给子进程注入这种默认值）时，httpx 会把它拼成非法 pattern
``all://*[::1]``，于是 ``httpx.AsyncClient`` / ``AsyncOpenAI`` **一构造就抛**
``InvalidURL``。provider 因此建不出来 → ``Runtime.start()`` 失败 → WebUI 页面
能打开但一直停在「启动失败」，看起来就是「WebUI 打不开」。
"""

from __future__ import annotations

import httpx
import pytest
from openai import AsyncOpenAI

from utils.proxy_env import sanitize_no_proxy_env

# ============================================================
# 归一化逻辑（直接喂字典，不碰真实环境）
# ============================================================


def test_bracketed_ipv6_is_unwrapped() -> None:
    env = {"NO_PROXY": "localhost,[::1]"}
    assert sanitize_no_proxy_env(env) == ["localhost", "::1"]
    assert env["NO_PROXY"] == "localhost,::1"


def test_bare_ipv6_left_untouched() -> None:
    env = {"NO_PROXY": "::1,127.0.0.1"}
    assert sanitize_no_proxy_env(env) == ["::1", "127.0.0.1"]
    # 本来就没问题就不该重写
    assert env["NO_PROXY"] == "::1,127.0.0.1"


def test_keeps_domains_cidr_and_wildcard() -> None:
    raw = "localhost,192.168.0.0/16,.example.com,*"
    env = {"NO_PROXY": raw}
    assert sanitize_no_proxy_env(env) == [
        "localhost",
        "192.168.0.0/16",
        ".example.com",
        "*",
    ]
    assert env["NO_PROXY"] == raw


def test_both_spellings_merged_and_deduped() -> None:
    env = {"NO_PROXY": "localhost,[::1]", "no_proxy": "LOCALHOST,10.0.0.0/8"}
    assert sanitize_no_proxy_env(env) == ["localhost", "::1", "10.0.0.0/8"]
    assert env["NO_PROXY"] == env["no_proxy"] == "localhost,::1,10.0.0.0/8"


def test_drops_unparseable_entries() -> None:
    env = {"NO_PROXY": "localhost,fo]o,,   "}
    assert sanitize_no_proxy_env(env) == ["localhost"]


def test_no_env_vars_is_noop() -> None:
    env: dict[str, str] = {}
    assert sanitize_no_proxy_env(env) == []
    # 不该凭空创建环境变量
    assert env == {}


# ============================================================
# 回归：httpx / openai 客户端能否构造
# ============================================================


async def test_clients_construct_after_sanitize(monkeypatch: pytest.MonkeyPatch) -> None:
    """不清理时客户端一构造就炸；清理后 httpx 和 openai 都能正常建出来。"""
    polluted = "localhost,127.0.0.1,::1,[::1]"
    monkeypatch.setenv("NO_PROXY", polluted)
    monkeypatch.setenv("no_proxy", polluted)

    # 先复现故障本身，确保这个测试真的在盯住那个 bug
    with pytest.raises(httpx.InvalidURL):
        httpx.AsyncClient()

    sanitize_no_proxy_env()

    async with httpx.AsyncClient() as client:
        assert client is not None
    async with AsyncOpenAI(api_key="x", base_url="https://api.deepseek.com/v1") as client:
        assert client is not None


def test_all_mount_patterns_are_parseable(monkeypatch: pytest.MonkeyPatch) -> None:
    """清理后 httpx 展开出的每个代理 pattern 都要能被解析。"""
    polluted = "localhost,127.0.0.1,192.168.0.0/16,::1,[::1]"
    monkeypatch.setenv("NO_PROXY", polluted)
    monkeypatch.setenv("no_proxy", polluted)

    sanitize_no_proxy_env()

    # 直接用 httpx 自己的展开逻辑（私有 API，但这正是出问题的那个函数，
    # 测试要盯的就是它的输出）
    mounts = httpx._utils.get_environment_proxies()  # noqa: SLF001
    assert "all://[::1]" in mounts, "IPv6 回环的绕过规则应当保留"
    for pattern in mounts:
        httpx.URL(pattern)  # 解析不了会抛 InvalidURL
