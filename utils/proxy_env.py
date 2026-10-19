"""代理环境变量预处理：剔除 httpx 解析不了的 ``NO_PROXY`` 条目。

背景
----
httpx 在**构造客户端时**就会把 ``NO_PROXY`` 展开成代理挂载表
（``httpx._utils.get_environment_proxies``），每个条目对应一个 URL pattern：
IPv6 字面量走 ``all://[{host}]``，其余非域名条目走 ``all://*{host}``。
判定用 ``is_ipv6_hostname``，而 ``[::1]`` 这种**带方括号**的写法过不了判定，
会掉进域名分支拼出 ``all://*[::1]`` —— 这个 pattern 解析不出端口，直接抛::

    httpx.InvalidURL: Invalid port: ':1]'

后果不是「访问某个地址时才报错」，而是 ``httpx.AsyncClient(...)`` /
``AsyncOpenAI(...)`` / ``AsyncAnthropic(...)`` **一构造就炸**：
``providers.registry.build_provider`` 失败 → ``core.Runtime.start()`` 失败 →
WebUI 的 Runtime 永远停在启动失败状态（页面能打开，但做什么都不行）。

``[::1]`` 本身是 curl 认的合法写法，所以这里不删条目，只把方括号脱掉还原成裸
IPv6 —— httpx 对 ``::1`` 的判定是正确的，「IPv6 回环不走代理」的原意得以保留。
"""

from __future__ import annotations

import logging
import os
from collections.abc import MutableMapping

logger = logging.getLogger(__name__)

# urllib 的 getproxies() 同时认这两种拼写，两边要保持一致。
_ENV_KEYS = ("NO_PROXY", "no_proxy")

# 脱掉方括号后仍含这些字符的条目不可能构成合法 pattern，直接丢弃。
_ILLEGAL_CHARS = "[]"

__all__ = ["sanitize_no_proxy_env"]


def sanitize_no_proxy_env(environ: MutableMapping[str, str] | None = None) -> list[str]:
    """就地清理 ``NO_PROXY`` / ``no_proxy``，返回清理后的条目列表。

    只动「httpx 会解析失败」的部分：脱掉 IPv6 的方括号、丢掉空条目和重复项；
    域名、IPv4、CIDR、``*`` 一律原样保留且顺序不变。

    ``environ`` 默认取 ``os.environ``，测试可传入自己的字典以免污染真实环境。
    两个变量都没设置时不做任何事（不会凭空创建环境变量）。
    """
    env = os.environ if environ is None else environ

    originals = {key: env[key] for key in _ENV_KEYS if key in env}
    if not originals:
        return []

    entries: list[str] = []
    seen: set[str] = set()
    for key in _ENV_KEYS:
        for raw in originals.get(key, "").split(","):
            entry = _normalize_entry(raw)
            if not entry:
                continue
            lowered = entry.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            entries.append(entry)

    cleaned = ",".join(entries)
    for key, value in originals.items():
        if value != cleaned:
            env[key] = cleaned
            logger.info(
                "清理 %s：%s → %s（httpx 无法解析带方括号的 IPv6 条目）",
                key,
                value or "(空)",
                cleaned or "(空)",
            )
    return entries


def _normalize_entry(raw: str) -> str:
    """归一化单个 no_proxy 条目；返回空串表示丢弃该条目。"""
    entry = raw.strip()
    if not entry:
        return ""

    # [::1] / [2001:db8::1] → ::1 / 2001:db8::1
    if len(entry) > 2 and entry.startswith("[") and entry.endswith("]"):
        entry = entry[1:-1].strip()

    if not entry or any(ch in entry for ch in _ILLEGAL_CHARS) or _has_whitespace(entry):
        logger.warning("丢弃无法解析的 NO_PROXY 条目：%r", raw.strip())
        return ""
    return entry


def _has_whitespace(value: str) -> bool:
    return any(ch.isspace() for ch in value)
