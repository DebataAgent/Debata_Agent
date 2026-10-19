"""测试 provider 注册中心与构造工厂。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app_config.schema import ProviderConfig
from app_config.secrets import SecretsManager
from providers import (
    AnthropicProvider,
    OpenAICompatProvider,
    ProviderError,
    ProviderRegistry,
    build_provider,
    known_protocols,
    register_protocol,
)
from providers.presets_loader import load_all_presets

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRESETS_DIR = PROJECT_ROOT / "providers" / "presets"


@pytest.fixture
def builtin_presets():
    return load_all_presets(PRESETS_DIR)


@pytest.fixture
def secrets(tmp_paths, fake_keyring):
    sm = SecretsManager(tmp_paths)
    sm.initialize()
    sm.set("ds_key", "sk-test123")
    sm.set("anthro_key", "sk-ant-test")
    return sm


# ============================================================
# build_provider 路径
# ============================================================


def test_build_from_preset_openai_compat(builtin_presets, secrets):
    cfg = ProviderConfig(preset="deepseek", api_key_id="ds_key")
    provider = build_provider("ds_main", cfg, secrets, builtin_presets)
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.name == "ds_main"
    assert provider.base_url == "https://api.deepseek.com/v1"
    assert provider.api_key == "sk-test123"


def test_build_from_preset_anthropic(builtin_presets, secrets):
    cfg = ProviderConfig(preset="anthropic", api_key_id="anthro_key")
    provider = build_provider("claude", cfg, secrets, builtin_presets)
    assert isinstance(provider, AnthropicProvider)
    assert provider.api_key == "sk-ant-test"


def test_build_overrides_base_url(builtin_presets, secrets):
    cfg = ProviderConfig(
        preset="deepseek",
        base_url="https://proxy.example.com/v1",
        api_key_id="ds_key",
    )
    provider = build_provider("dsmain", cfg, secrets, builtin_presets)
    assert provider.base_url == "https://proxy.example.com/v1"


def test_build_custom_provider(secrets):
    cfg = ProviderConfig(
        protocol="openai_compat",
        base_url="https://custom.example.com/v1",
        api_key_id="ds_key",
    )
    provider = build_provider("custom", cfg, secrets, {})
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.base_url == "https://custom.example.com/v1"


def test_custom_provider_defaults_to_no_reasoning_style(secrets):
    """无 preset 的自定义 provider（中转站 / MiMo 类网关）不得带厂商私有思考字段。

    OpenAICompatProvider.reasoning_style 的默认值是 thinking_extra_body（DeepSeek 系），
    若 build_provider 不覆盖它，中转站会收到 {"thinking": {"type": "disabled"}} 而可能 400。
    """
    cfg = ProviderConfig(
        protocol="openai_compat",
        base_url="https://relay.example.com/v1",
        api_key_id="ds_key",
    )
    provider = build_provider("relay", cfg, secrets, {})
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.reasoning_style == "none"
    assert provider.known_model_ids == set()
    assert provider.reasoning_model_ids == set()


def test_custom_provider_sends_no_thinking_field(secrets):
    """承上：实际构造出的 extra_body 必须是空的。"""
    from providers import ReasoningConfig

    cfg = ProviderConfig(
        protocol="openai_compat",
        base_url="https://relay.example.com/v1",
        api_key_id="ds_key",
    )
    provider = build_provider("relay", cfg, secrets, {})
    assert provider._build_reasoning_extra_body(None, model="whatever") == {}
    assert (
        provider._build_reasoning_extra_body(
            ReasoningConfig(enabled=True), model="whatever"
        )
        == {}
    )


def test_preset_still_injects_thinking_field(builtin_presets, secrets):
    """对照组：有 preset 的 DeepSeek 仍应发 thinking 字段，别把功能一起修掉。"""
    from providers import ReasoningConfig

    cfg = ProviderConfig(preset="deepseek", api_key_id="ds_key")
    provider = build_provider("ds", cfg, secrets, builtin_presets)
    assert provider.reasoning_style == "thinking_extra_body"
    assert provider._build_reasoning_extra_body(
        ReasoningConfig(enabled=False), model="deepseek-flash"
    ) == {"thinking": {"type": "disabled"}}


def test_config_reasoning_style_overrides_preset(builtin_presets, secrets):
    """显式配置优先于 preset 默认值（例如用 DeepSeek 预设走中转站）。"""
    cfg = ProviderConfig(
        preset="deepseek",
        base_url="https://relay.example.com/v1",
        api_key_id="ds_key",
        reasoning_style="none",
    )
    provider = build_provider("relay", cfg, secrets, builtin_presets)
    assert provider.reasoning_style == "none"
    # known_model_ids 仍来自 preset（base_url 是覆盖，不是抛弃 preset）
    assert "deepseek-flash" in provider.known_model_ids


def test_config_reasoning_style_rejects_unknown_value():
    """非法取值由 Literal 拦下，避免拼错后静默退回默认行为。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProviderConfig(
            protocol="openai_compat",
            base_url="https://x.example.com/v1",
            reasoning_style="thinking_extra_header",  # 文档里写过但未实现的值
        )


def test_build_unknown_preset_raises(secrets):
    cfg = ProviderConfig(preset="totally_unknown", api_key_id="ds_key")
    with pytest.raises(ProviderError, match="未知预设"):
        build_provider("x", cfg, secrets, {})


def test_build_missing_api_key_warns(builtin_presets, secrets, caplog):
    """密钥不存在时应警告但仍构造（用空 key）。"""
    import logging
    caplog.set_level(logging.WARNING)
    cfg = ProviderConfig(preset="deepseek", api_key_id="missing")
    provider = build_provider("ds", cfg, secrets, builtin_presets)
    assert provider.api_key == ""
    assert "找不到" in caplog.text


def test_build_passes_reasoning_style(builtin_presets, secrets):
    """openai_compat 协议时，reasoning_style 应从 preset 传给 provider。"""
    cfg = ProviderConfig(preset="qwen", api_key_id="ds_key")
    provider = build_provider("qwen", cfg, secrets, builtin_presets)
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.reasoning_style == "qwen_enable_thinking"
    assert "qwen3-max" in provider.reasoning_model_ids
    assert "qwen3-turbo" in provider.known_model_ids
    assert "qwen3-turbo" not in provider.reasoning_model_ids


# ============================================================
# ProviderRegistry
# ============================================================


def test_registry_register_and_get(secrets, builtin_presets):
    reg = ProviderRegistry()
    cfg = ProviderConfig(preset="deepseek", api_key_id="ds_key")
    p = build_provider("ds", cfg, secrets, builtin_presets)
    reg.register(p)

    assert reg.has("ds")
    assert reg.get("ds") is p
    assert "ds" in reg.list_names()


def test_registry_duplicate_raises(secrets, builtin_presets):
    reg = ProviderRegistry()
    cfg = ProviderConfig(preset="deepseek", api_key_id="ds_key")
    reg.register(build_provider("ds", cfg, secrets, builtin_presets))
    with pytest.raises(ValueError, match="重复"):
        reg.register(build_provider("ds", cfg, secrets, builtin_presets))


def test_registry_get_missing_raises():
    reg = ProviderRegistry()
    with pytest.raises(KeyError):
        reg.get("nope")


def test_registry_load_presets():
    reg = ProviderRegistry()
    reg.load_presets(PRESETS_DIR)
    assert "deepseek" in reg.presets
    assert len(reg.presets) >= 7


def test_registry_build_from_config(secrets):
    reg = ProviderRegistry()
    reg.load_presets(PRESETS_DIR)

    cfgs = {
        "ds_main": ProviderConfig(preset="deepseek", api_key_id="ds_key"),
        "claude": ProviderConfig(preset="anthropic", api_key_id="anthro_key"),
    }
    reg.build_from_config(cfgs, secrets)
    assert reg.has("ds_main")
    assert reg.has("claude")


@pytest.mark.asyncio
async def test_registry_close_all(secrets, builtin_presets):
    reg = ProviderRegistry()
    cfg = ProviderConfig(preset="deepseek", api_key_id="ds_key")
    reg.register(build_provider("ds", cfg, secrets, builtin_presets))
    # 不应抛异常
    await reg.close_all()


# ============================================================
# 协议注册
# ============================================================


def test_known_protocols():
    protos = known_protocols()
    assert "openai_compat" in protos
    assert "anthropic" in protos


def test_register_protocol_duplicate_raises():
    with pytest.raises(ValueError, match="已注册"):
        register_protocol("openai_compat", lambda **k: None)
