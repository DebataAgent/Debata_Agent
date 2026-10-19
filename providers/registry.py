"""Provider 实例的注册中心与构造工厂。

启动流程：
    1. load_all_presets() —— 扫描 providers/presets/*/preset.yaml
    2. 对 config.providers 中每条记录：
        - 如指定 preset，从预设取 protocol/base_url
        - 否则用用户填的 protocol/base_url
        - 通过 PROTOCOL_REGISTRY[protocol] 构造对应类
    3. 注册到 ProviderRegistry，按 ID 查找

Provider 实例是异步资源（含 HTTP 连接池），需在停机时调用 close_all() 优雅关闭。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from app_config.schema import ProviderConfig
from app_config.secrets import SecretsManager
from utils.proxy_env import sanitize_no_proxy_env

from .base import IProvider, ProviderError
from .presets_loader import ProviderPreset, load_all_presets, load_presets_for
from .protocols import AnthropicProvider, OpenAICompatProvider

logger = logging.getLogger(__name__)


# 协议名 → 构造函数。新增协议时在此注册。
ProtocolFactory = Callable[..., IProvider]

PROTOCOL_REGISTRY: dict[str, ProtocolFactory] = {
    "openai_compat": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
}


def register_protocol(protocol_name: str, factory: ProtocolFactory) -> None:
    """新增一个协议实现。"""
    if protocol_name in PROTOCOL_REGISTRY:
        raise ValueError(f"协议 {protocol_name} 已注册")
    PROTOCOL_REGISTRY[protocol_name] = factory


def known_protocols() -> list[str]:
    return list(PROTOCOL_REGISTRY.keys())


def normalize_base_url(url: str, protocol: str) -> str:
    """OpenAI 兼容协议的 base_url 若不以 /v{N} 结尾则自动补 /v1。"""
    if protocol == "openai_compat":
        url = url.rstrip("/")
        if not re.search(r"/v\d+$", url):
            url += "/v1"
    return url


# ============================================================
# 构造逻辑
# ============================================================


def build_provider(
    provider_id: str,
    cfg: ProviderConfig,
    secrets: SecretsManager,
    presets: dict[str, ProviderPreset],
) -> IProvider:
    """根据 ProviderConfig 构造一个 IProvider 实例。"""

    # 协议层用 httpx 建连接池，而 httpx 在**构造客户端时**就展开 NO_PROXY：
    # 条目里出现带方括号的 IPv6（[::1]，Cherry Studio 等运行时会注入）会拼出非法
    # pattern all://*[::1]，客户端一构造就抛 InvalidURL。入口脚本（main.py /
    # webui_server.py）已经清理过一次；这里再兜一层且幂等，保证直接调用本函数
    # 的场景（pytest、内嵌用法）同样不会被环境变量炸掉。
    sanitize_no_proxy_env()

    # 解析协议、base_url、reasoning_style
    preset: ProviderPreset | None = None
    if cfg.preset:
        preset = presets.get(cfg.preset.lower())
        if preset is None:
            raise ProviderError(
                f"未知预设: {cfg.preset}。已加载的预设: {sorted(presets.keys())}"
            )

    protocol = cfg.protocol or (preset.protocol if preset else None)
    if protocol is None:
        raise ProviderError(f"provider {provider_id} 既无 preset 也无 protocol")
    base_url = cfg.base_url or (preset.base_url if preset else None)
    if not base_url:
        raise ProviderError(f"provider {provider_id} 缺 base_url")
    base_url = normalize_base_url(base_url, protocol)

    factory = PROTOCOL_REGISTRY.get(protocol)
    if factory is None:
        raise ProviderError(
            f"未知协议: {protocol}。已注册: {list(PROTOCOL_REGISTRY.keys())}"
        )

    api_key: str | None = None
    if cfg.api_key_id:
        api_key = secrets.get(cfg.api_key_id)
        if api_key is None:
            logger.warning(
                f"Provider {provider_id} 引用的 api_key_id={cfg.api_key_id} "
                f"在 secrets 中找不到，将以空密钥构造"
            )

    kwargs: dict[str, Any] = {
        "name": provider_id,
        "base_url": base_url,
        "api_key": api_key,
        "timeout": cfg.timeout_seconds,
        "extra_headers": dict(cfg.extra_headers),
    }

    # openai_compat 专属参数。
    # 必须无条件传 reasoning_style：OpenAICompatProvider 的默认值是
    # "thinking_extra_body"，若不覆盖，无 preset 的自定义 provider（中转站 / MiMo 等）
    # 会把 {"thinking": {"type": "disabled"}} 发给根本不认识这个字段的网关，严格网关直接 400。
    # 优先级：配置显式指定 > preset 默认 > none（不认识就什么都不发）。
    if protocol == "openai_compat":
        if cfg.reasoning_style is not None:
            kwargs["reasoning_style"] = cfg.reasoning_style
        elif preset is not None:
            kwargs["reasoning_style"] = preset.reasoning_style
        else:
            kwargs["reasoning_style"] = "none"
        kwargs["known_model_ids"] = {m.id for m in preset.models} if preset else set()
        kwargs["reasoning_model_ids"] = (
            {m.id for m in preset.models if "reasoning" in m.capabilities}
            if preset
            else set()
        )

    return factory(**kwargs)


# ============================================================
# 注册中心
# ============================================================


class ProviderRegistry:
    """运行时管理所有 IProvider 实例。"""

    def __init__(self) -> None:
        self._providers: dict[str, IProvider] = {}
        self._presets: dict[str, ProviderPreset] = {}

    def load_presets(self, presets_dir: Path, *, extra_dirs: Sequence[Path] = ()) -> None:
        self._presets = load_all_presets(presets_dir, extra_dirs=extra_dirs)

    def load_presets_for(self, paths: Any) -> None:
        """按 AppPaths 加载内置 + 用户自定义预设（推荐的调用方式）。"""
        self._presets = load_presets_for(paths)

    @property
    def presets(self) -> dict[str, ProviderPreset]:
        return self._presets

    def register(self, provider: IProvider) -> None:
        if provider.name in self._providers:
            raise ValueError(f"Provider 名重复: {provider.name}")
        self._providers[provider.name] = provider
        logger.info(f"Provider 已注册: {provider.name} ({type(provider).__name__})")

    def get(self, name: str) -> IProvider:
        if name not in self._providers:
            raise KeyError(
                f"未知 provider: {name}。已注册: {list(self._providers.keys())}"
            )
        return self._providers[name]

    def has(self, name: str) -> bool:
        return name in self._providers

    def list_names(self) -> list[str]:
        return list(self._providers.keys())

    def build_from_config(
        self,
        providers_cfg: dict[str, ProviderConfig],
        secrets: SecretsManager,
    ) -> None:
        """根据配置批量构造所有 provider 实例并注册。"""
        for pid, cfg in providers_cfg.items():
            try:
                provider = build_provider(pid, cfg, secrets, self._presets)
                self.register(provider)
            except Exception as e:
                logger.error(f"构造 provider {pid} 失败: {e}")
                raise

    async def close_all(self) -> None:
        """关闭所有 provider 的底层连接。"""
        if not self._providers:
            return
        await asyncio.gather(
            *(p.aclose() for p in self._providers.values()),
            return_exceptions=True,
        )
        logger.info(f"已关闭 {len(self._providers)} 个 provider")
