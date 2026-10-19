"""Provider 增删改（webui/provider_admin.py）测试。

重点是三件容易出错的事：
    1. 改动失败必须整体回滚，不能留下"半改"的配置
    2. 密钥只进 SecretsManager，响应里只有 has_key
    3. 删 provider 时默认不动密钥
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app_config.schema import ProviderConfig, RootConfig
from webui.provider_admin import (
    ProviderAdminError,
    ProviderInUseError,
    ProviderPayload,
    create_provider,
    delete_provider,
    key_id_for,
    list_providers,
    provider_references,
    update_provider,
    validate_provider_id,
)


class FakeSecrets:
    """最小 SecretsManager 替身，只实现 provider_admin 用到的三个方法。"""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.data = dict(initial or {})

    def get(self, key_id: str):
        return self.data.get(key_id)

    def set(self, key_id: str, value: str) -> None:
        self.data[key_id] = value

    def delete(self, key_id: str) -> None:
        self.data.pop(key_id, None)


def _minimal_config(**overrides) -> RootConfig:
    """构造一份能通过校验的最小配置。"""
    chat = {
        "provider": "deepseek_main",
        "model": "deepseek-v4-flash",
    }
    chat.update(overrides.get("chat", {}))
    return RootConfig.model_validate(
        {
            "persona": {"name": "test_bot"},
            "adapters": {"default": {"type": "napcat"}},
            "providers": {
                "deepseek_main": {"preset": "deepseek", "api_key_id": "deepseek_main_key"},
            },
            "agents": {"chat": chat},
        }
    )


@pytest.fixture
def presets():
    from providers.presets_loader import load_all_presets

    root = Path(__file__).resolve().parent.parent.parent
    return load_all_presets(root / "providers" / "presets")


# ============================================================
# id 校验
# ============================================================


@pytest.mark.parametrize("good", ["relay01", "mimo", "my-relay", "a", "a_b_c", "x" * 64])
def test_validate_provider_id_accepts(good):
    assert validate_provider_id(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "  ",
        "Relay",        # 大写
        "1relay",       # 数字开头
        "relay.01",     # 点
        "relay/01",     # 斜杠
        "relay 01",     # 空格
        "relay@01",
        "x" * 65,
        "default",      # 保留名
        "napcat",
        "test",
    ],
)
def test_validate_provider_id_rejects(bad):
    with pytest.raises(ProviderAdminError):
        validate_provider_id(bad)


def test_key_id_for():
    assert key_id_for("relay01") == "relay01_key"


# ============================================================
# 新建
# ============================================================


def test_create_preset_provider(presets):
    cfg = _minimal_config()
    secrets = FakeSecrets()
    pid = create_provider(
        cfg, secrets,
        ProviderPayload(id="mimo", preset="mimo", api_key="sk-mimo"),
        presets=presets,
    )
    assert pid == "mimo"
    assert cfg.providers["mimo"].preset == "mimo"
    assert cfg.providers["mimo"].api_key_id == "mimo_key"
    assert secrets.data["mimo_key"] == "sk-mimo"


def test_create_custom_relay_provider(presets):
    """中转站：不选预设，只给协议 + Base URL。"""
    cfg = _minimal_config()
    secrets = FakeSecrets()
    create_provider(
        cfg, secrets,
        ProviderPayload(
            id="relay01",
            protocol="openai_compat",
            base_url="https://relay.example.com/v1",
            api_key="sk-relay",
        ),
        presets=presets,
    )
    relay = cfg.providers["relay01"]
    assert relay.preset is None
    assert relay.base_url == "https://relay.example.com/v1"
    assert secrets.data["relay01_key"] == "sk-relay"


def test_create_without_key_still_records_key_id(presets):
    """先建 provider、后填密钥——key_id 先占好，写密钥时不用再猜。"""
    cfg = _minimal_config()
    create_provider(
        cfg, FakeSecrets(),
        ProviderPayload(id="relay01", protocol="openai_compat",
                        base_url="https://relay.example.com/v1"),
        presets=presets,
    )
    assert cfg.providers["relay01"].api_key_id == "relay01_key"


def test_create_duplicate_id_rejected(presets):
    cfg = _minimal_config()
    with pytest.raises(ProviderAdminError, match="已存在"):
        create_provider(
            cfg, FakeSecrets(),
            ProviderPayload(id="deepseek_main", preset="deepseek"),
            presets=presets,
        )


def test_create_unknown_preset_rejected(presets):
    cfg = _minimal_config()
    with pytest.raises(ProviderAdminError, match="未知预设"):
        create_provider(
            cfg, FakeSecrets(),
            ProviderPayload(id="x01", preset="no_such_preset"),
            presets=presets,
        )


def test_create_without_protocol_or_base_url_rejected(presets):
    """无 preset 时必须给 protocol + base_url，否则 ProviderConfig 校验不过。"""
    cfg = _minimal_config()
    with pytest.raises(ProviderAdminError, match="protocol|base_url"):
        create_provider(cfg, FakeSecrets(), ProviderPayload(id="x01"), presets=presets)


def test_create_rolls_back_on_failure(presets):
    """校验失败时配置必须原样不动。"""
    cfg = _minimal_config()
    before = cfg.model_dump()
    with pytest.raises(ProviderAdminError):
        create_provider(cfg, FakeSecrets(), ProviderPayload(id="x01"), presets=presets)
    assert cfg.model_dump() == before
    assert "x01" not in cfg.providers


# ============================================================
# 编辑
# ============================================================


def test_update_base_url(presets):
    cfg = _minimal_config()
    changed = update_provider(
        cfg, FakeSecrets(), "deepseek_main",
        ProviderPayload(base_url="https://relay.example.com/v1"),
        presets=presets,
    )
    assert changed == ["base_url"]
    assert cfg.providers["deepseek_main"].base_url == "https://relay.example.com/v1"
    # preset 仍在，只是 base_url 被覆盖
    assert cfg.providers["deepseek_main"].preset == "deepseek"


def test_update_only_touches_provided_fields(presets):
    """没出现在请求里的字段不能被动。"""
    cfg = _minimal_config()
    cfg.providers["deepseek_main"].display_name = "原来的名字"
    update_provider(
        cfg, FakeSecrets(), "deepseek_main",
        ProviderPayload(timeout_seconds=30.0),
        presets=presets,
    )
    assert cfg.providers["deepseek_main"].display_name == "原来的名字"
    assert cfg.providers["deepseek_main"].timeout_seconds == 30.0


def test_update_reasoning_style(presets):
    """中转站要把 reasoning_style 设为 none，这是能安全接入未知网关的关键。"""
    cfg = _minimal_config()
    update_provider(
        cfg, FakeSecrets(), "deepseek_main",
        ProviderPayload(reasoning_style="none"),
        presets=presets,
    )
    assert cfg.providers["deepseek_main"].reasoning_style == "none"


def test_update_rejects_unknown_reasoning_style(presets):
    cfg = _minimal_config()
    with pytest.raises(ProviderAdminError):
        update_provider(
            cfg, FakeSecrets(), "deepseek_main",
            ProviderPayload(reasoning_style="thinking_extra_header"),
            presets=presets,
        )


def test_update_empty_string_clears_field(presets):
    """前端把输入框留空 = 清空，而不是写出 base_url=""。"""
    cfg = _minimal_config()
    update_provider(
        cfg, FakeSecrets(), "deepseek_main",
        ProviderPayload(display_name=""),
        presets=presets,
    )
    assert cfg.providers["deepseek_main"].display_name is None


def test_update_missing_provider(presets):
    cfg = _minimal_config()
    with pytest.raises(ProviderAdminError, match="没有"):
        update_provider(cfg, FakeSecrets(), "nope", ProviderPayload(), presets=presets)


def test_update_rotates_key(presets):
    cfg = _minimal_config()
    secrets = FakeSecrets({"deepseek_main_key": "sk-old"})
    changed = update_provider(
        cfg, secrets, "deepseek_main", ProviderPayload(api_key="sk-new"), presets=presets
    )
    assert "api_key" in changed
    assert secrets.data["deepseek_main_key"] == "sk-new"


def test_update_clears_key(presets):
    cfg = _minimal_config()
    secrets = FakeSecrets({"deepseek_main_key": "sk-old"})
    update_provider(
        cfg, secrets, "deepseek_main", ProviderPayload(clear_api_key=True), presets=presets
    )
    assert "deepseek_main_key" not in secrets.data
    assert cfg.providers["deepseek_main"].api_key_id is None


def test_update_rolls_back_entire_change_set(presets):
    """一次请求改多个字段时，只要有一个不合法，整批都不生效。

    （preset 与 protocol/base_url 是可以共存的——preset 提供默认、字段就地覆盖，
    所以清空 base_url 并不是错误。这里用非法的 reasoning_style 触发失败。）
    """
    cfg = _minimal_config()
    before = cfg.model_dump()
    with pytest.raises(ProviderAdminError):
        update_provider(
            cfg, FakeSecrets(), "deepseek_main",
            ProviderPayload(display_name="新名字", reasoning_style="thinking_extra_header"),
            presets=presets,
        )
    # display_name 是合法的，但同批里有不合法的，也不能留下
    assert cfg.model_dump() == before
    assert cfg.providers["deepseek_main"].display_name is None


def test_update_switching_preset_is_allowed(presets):
    """preset 与 protocol/base_url 可共存：换 preset 时清掉覆盖字段是合法的。"""
    cfg = _minimal_config()
    changed = update_provider(
        cfg, FakeSecrets(), "deepseek_main",
        ProviderPayload(preset="mimo", protocol=None, base_url=None),
        presets=presets,
    )
    assert "preset" in changed
    assert cfg.providers["deepseek_main"].preset == "mimo"
    assert cfg.providers["deepseek_main"].base_url is None  # 回落到 mimo 预设的地址


# ============================================================
# 删除
# ============================================================


def test_delete_provider(presets):
    cfg = _minimal_config()
    create_provider(
        cfg, FakeSecrets(),
        ProviderPayload(id="relay01", protocol="openai_compat",
                        base_url="https://relay.example.com/v1"),
        presets=presets,
    )
    assert delete_provider(cfg, FakeSecrets(), "relay01") is True
    assert "relay01" not in cfg.providers


def test_delete_refuses_when_referenced():
    """被 Agent 引用时不能删——否则启动时 validate_references 会直接炸。"""
    cfg = _minimal_config()
    with pytest.raises(ProviderInUseError) as excinfo:
        delete_provider(cfg, FakeSecrets(), "deepseek_main")
    assert excinfo.value.used_by == ["agents.chat"]
    assert "deepseek_main" in cfg.providers


def test_delete_keeps_key_by_default():
    """删配置常是为了重建（比如换 base_url），顺手删密钥会让用户还得重新申请。"""
    cfg = _minimal_config()
    secrets = FakeSecrets({"deepseek_main_key": "sk-keep"})
    cfg.agents.chat.provider = "other"
    cfg.providers["other"] = ProviderConfig(
        protocol="openai_compat", base_url="https://other.example.com/v1"
    )
    delete_provider(cfg, secrets, "deepseek_main")
    assert secrets.data["deepseek_main_key"] == "sk-keep"


def test_delete_drops_key_when_asked():
    cfg = _minimal_config()
    secrets = FakeSecrets({"deepseek_main_key": "sk-drop"})
    cfg.agents.chat.provider = "other"
    cfg.providers["other"] = ProviderConfig(
        protocol="openai_compat", base_url="https://other.example.com/v1"
    )
    delete_provider(cfg, secrets, "deepseek_main", drop_key=True)
    assert "deepseek_main_key" not in secrets.data


def test_delete_missing():
    cfg = _minimal_config()
    with pytest.raises(ProviderAdminError, match="没有"):
        delete_provider(cfg, FakeSecrets(), "nope")


# ============================================================
# 引用查询
# ============================================================


def test_provider_references_includes_features():
    cfg = _minimal_config()
    cfg.features.vision.provider = "deepseek_main"
    refs = provider_references(cfg, "deepseek_main")
    assert "agents.chat" in refs
    assert "features.vision" in refs


def test_provider_references_empty():
    cfg = _minimal_config()
    assert provider_references(cfg, "nobody") == []


# ============================================================
# 列表：绝不回显密钥
# ============================================================


def test_list_providers_never_exposes_key():
    cfg = _minimal_config()
    secrets = FakeSecrets({"deepseek_main_key": "sk-super-secret"})
    rows = list_providers(cfg, secrets)
    assert len(rows) == 1
    row = rows[0]
    assert row["has_key"] is True
    assert row["api_key_id"] == "deepseek_main_key"
    # 整个响应里不能出现密钥内容
    assert "sk-super-secret" not in repr(rows)


def test_list_providers_has_key_false_when_missing():
    cfg = _minimal_config()
    rows = list_providers(cfg, FakeSecrets())
    assert rows[0]["has_key"] is False


def test_list_providers_reports_used_by():
    cfg = _minimal_config()
    rows = list_providers(cfg, FakeSecrets())
    assert rows[0]["used_by"] == ["agents.chat"]


def test_list_providers_includes_health():
    cfg = _minimal_config()

    class Health:
        status = "healthy"
        message = "就位"
        latency_ms = 42

    rows = list_providers(cfg, FakeSecrets(), health={"deepseek_main": Health()})
    assert rows[0]["health"] == {"status": "healthy", "message": "就位", "latency_ms": 42}


def test_list_providers_health_absent():
    cfg = _minimal_config()
    assert list_providers(cfg, FakeSecrets())[0]["health"] is None


# ============================================================
# 探测目标解析
# ============================================================


def test_resolve_probe_target_uses_payload(presets):
    from webui.provider_admin import resolve_probe_target

    cfg = _minimal_config()
    target = resolve_probe_target(
        cfg, FakeSecrets(), presets,
        ProviderPayload(
            protocol="openai_compat",
            base_url="https://relay.example.com",
            api_key="sk-x",
            model="my-model",
        ),
    )
    assert target.protocol == "openai_compat"
    # 不带 /v{N} 时自动补 /v1
    assert target.base_url == "https://relay.example.com/v1"
    assert target.api_key == "sk-x"
    assert target.model == "my-model"


def test_resolve_probe_target_falls_back_to_saved(presets):
    from webui.provider_admin import resolve_probe_target

    cfg = _minimal_config()
    target = resolve_probe_target(
        cfg, FakeSecrets({"deepseek_main_key": "sk-saved"}), presets,
        ProviderPayload(), provider_id="deepseek_main",
    )
    assert target.base_url == "https://api.deepseek.com/v1"
    assert target.api_key == "sk-saved"
    assert target.model == "deepseek-v4-flash"  # 取自 agents.chat.model


def test_resolve_probe_target_requires_key(presets):
    from webui.provider_admin import resolve_probe_target

    cfg = _minimal_config()
    with pytest.raises(ProviderAdminError, match="API Key"):
        resolve_probe_target(
            cfg, FakeSecrets(), presets, ProviderPayload(), provider_id="deepseek_main"
        )


def test_resolve_probe_target_requires_base_url(presets):
    from webui.provider_admin import resolve_probe_target

    cfg = _minimal_config()
    with pytest.raises(ProviderAdminError, match="Base URL"):
        resolve_probe_target(cfg, FakeSecrets(), presets, ProviderPayload(protocol="openai_compat"))


def test_resolve_probe_target_requires_model(presets):
    """探测要真发一次请求，没有模型名就没法测。"""
    from webui.provider_admin import resolve_probe_target

    cfg = _minimal_config()
    with pytest.raises(ProviderAdminError, match="模型"):
        resolve_probe_target(
            cfg, FakeSecrets(), presets,
            ProviderPayload(
                protocol="openai_compat",
                base_url="https://relay.example.com/v1",
                api_key="sk-x",
            ),
        )
