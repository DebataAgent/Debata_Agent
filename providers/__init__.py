"""Debata_Agent 多提供商系统。

公开 API：
    IProvider             —— 提供商抽象
    CompletionResult      —— 调用结果统一格式
    ReasoningConfig       —— 思考配置
    ProviderRegistry      —— 提供商实例管理
    build_provider        —— 根据 ProviderConfig 构造一个 provider
    load_all_presets      —— 加载预设（支持内置 + 用户目录合并）
    load_presets_for      —— 按 AppPaths 加载预设（推荐）
    fetch_model_list      —— 从远端拉取可用模型 ID 列表
    fetch_model_infos     —— 同上，并用能力矩阵补齐能力字段
    register_protocol     —— 注册新协议实现
"""

from .base import (
    CompletionResult,
    IProvider,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnsupportedError,
    ReasoningConfig,
    ToolCall,
    Usage,
)
from .health import (
    ProviderHealth,
    probe_embedding_endpoint,
    probe_embedding_provider_instance,
    probe_provider_endpoint,
    probe_provider_instance,
)
from .model_capabilities import (
    capability_badges,
    known_model_ids,
    model_capability,
    recommended_model,
)
from .model_fetcher import FetchedModel, fetch_model_infos, fetch_model_list
from .presets_loader import (
    ModelInfo,
    ProviderPreset,
    load_all_presets,
    load_presets_for,
)
from .protocols import AnthropicProvider, OpenAICompatProvider
from .registry import (
    PROTOCOL_REGISTRY,
    ProviderRegistry,
    build_provider,
    known_protocols,
    register_protocol,
)

__all__ = [
    # base
    "IProvider",
    "CompletionResult",
    "ReasoningConfig",
    "ToolCall",
    "Usage",
    "ProviderError",
    "ProviderAuthError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ProviderUnsupportedError",
    # presets
    "ProviderPreset",
    "ModelInfo",
    "load_all_presets",
    "load_presets_for",
    # 模型清单与能力矩阵
    "FetchedModel",
    "fetch_model_infos",
    "fetch_model_list",
    "model_capability",
    "recommended_model",
    "known_model_ids",
    "capability_badges",
    "ProviderHealth",
    "probe_embedding_endpoint",
    "probe_embedding_provider_instance",
    "probe_provider_endpoint",
    "probe_provider_instance",
    # protocols
    "OpenAICompatProvider",
    "AnthropicProvider",
    # registry
    "ProviderRegistry",
    "PROTOCOL_REGISTRY",
    "build_provider",
    "register_protocol",
    "known_protocols",
]
