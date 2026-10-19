"""向导写配置（RuntimeHost.write_initial_config）测试。

这里锁的是两条曾经出过问题的行为：

    1. **重跑向导不能清空用户后来加的 provider。**
       原实现是 ``merged.providers = cfg.providers``（整段赋值），用户先在设置页
       加了个中转站、再回头重跑向导改主模型，那个 provider 就没了，
       连带引用它的 Agent 一起失效。
    2. **选了 whitelist 就得把名单写进去。**
       前端曾经把 qq_ids/group_ids 写死成空数组，等于选了白名单却谁都不放行。
"""

from __future__ import annotations

import pytest

from app_config.loader import load_config
from webui.runtime_host import RuntimeHost


@pytest.fixture
def host(tmp_path, fake_keyring):
    return RuntimeHost(project_root=tmp_path)


def _payload(**overrides) -> dict:
    payload = {
        "providers": [
            {"id": "deepseek_main", "preset": "deepseek", "api_key": "sk-test"},
        ],
        "chat": {"provider": "deepseek_main", "model": "deepseek-v4-flash"},
        "persona": "debata",
        "adapter": {"mode": "client", "host": "127.0.0.1", "port": 3000},
    }
    payload.update(overrides)
    return payload


def _read(host) -> object:
    return load_config(host.build_paths(), set_global=False)


# ============================================================
# 基本写入
# ============================================================


def test_writes_minimal_config(host):
    result = host.write_initial_config(_payload())
    assert result["ok"] is True
    assert result["provider_id"] == "deepseek_main"
    assert result["model"] == "deepseek-v4-flash"

    cfg = _read(host)
    assert cfg.providers["deepseek_main"].preset == "deepseek"
    assert cfg.agents.chat.model == "deepseek-v4-flash"


def test_requires_at_least_one_provider(host):
    with pytest.raises(ValueError, match="至少"):
        host.write_initial_config(_payload(providers=[]))


def test_provider_id_cannot_be_empty(host):
    with pytest.raises(ValueError, match="Provider ID"):
        host.write_initial_config(_payload(providers=[{"preset": "deepseek"}]))


def test_relay_provider_round_trips(host):
    """中转站：自定义 base_url + reasoning_style=none（不发厂商私有字段）。"""
    host.write_initial_config(_payload(providers=[{
        "id": "relay01",
        "protocol": "openai_compat",
        "base_url": "https://relay.example.com/v1",
        "api_key": "sk-relay",
        "reasoning_style": "none",
    }], chat={"provider": "relay01", "model": "m"}))
    cfg = _read(host)
    relay = cfg.providers["relay01"]
    assert relay.base_url == "https://relay.example.com/v1"
    assert relay.reasoning_style == "none"
    assert relay.api_key_id == "relay01_key"


# ============================================================
# providers 合并语义（核心回归）
# ============================================================


def test_rerun_keeps_user_added_provider(host):
    """重跑向导不能把用户后来加的 provider 清掉。"""
    host.write_initial_config(_payload())
    host.write_initial_config(_payload(providers=[
        {"id": "relay01", "protocol": "openai_compat",
         "base_url": "https://relay.example.com/v1", "api_key": "sk-r"},
    ], chat={"provider": "relay01", "model": "m"}))

    cfg = _read(host)
    assert "deepseek_main" in cfg.providers, "第一次配的 provider 被清掉了"
    assert "relay01" in cfg.providers


def test_rerun_updates_same_id_in_place(host):
    host.write_initial_config(_payload())
    host.write_initial_config(_payload(providers=[
        {"id": "deepseek_main", "preset": "deepseek",
         "base_url": "https://relay.example.com/v1", "api_key": "sk-new"},
    ]))
    cfg = _read(host)
    assert len(cfg.providers) == 1
    assert cfg.providers["deepseek_main"].base_url == "https://relay.example.com/v1"


def test_replace_providers_flag_forces_reset(host):
    """显式要求重置时才整段替换（给"重跑向导清干净"留的口子）。"""
    host.write_initial_config(_payload())
    host.write_initial_config(_payload(replace_providers=True, providers=[
        {"id": "only_one", "protocol": "openai_compat",
         "base_url": "https://x.example.com/v1", "api_key": "sk-x"},
    ], chat={"provider": "only_one", "model": "m"}))
    cfg = _read(host)
    assert set(cfg.providers) == {"only_one"}


def test_rejects_agent_referencing_unknown_provider(host):
    """引用不存在的 provider 必须在写盘前拦住，而不是留到启动时炸。"""
    with pytest.raises(ValueError):
        host.write_initial_config(_payload(
            chat={"provider": "deepseek_main", "model": "m"},
            agents={"proactive": {"enabled": True, "provider": "ghost"}},
        ))


# ============================================================
# Agent 参数
# ============================================================


def test_chat_top_p_and_reasoning(host):
    host.write_initial_config(_payload(chat={
        "provider": "deepseek_main", "model": "deepseek-v4-flash",
        "top_p": 0.9, "reasoning": True, "reasoning_budget": "high",
    }))
    chat = _read(host).agents.chat
    assert chat.top_p == 0.9
    assert chat.reasoning.enabled is True
    assert chat.reasoning.budget == "high"


def test_sub_agents_can_use_their_own_provider(host):
    """子 Agent 可以独立选 provider/model，不必跟随主模型。"""
    host.write_initial_config(_payload(
        providers=[
            {"id": "deepseek_main", "preset": "deepseek", "api_key": "k1"},
            {"id": "relay01", "protocol": "openai_compat",
             "base_url": "https://relay.example.com/v1", "api_key": "k2"},
        ],
        agents={
            "proactive": {"enabled": True, "temperature": 0.2},
            "summary": {"enabled": False},
            "persona_gen": {"enabled": True, "provider": "deepseek_main",
                            "model": "deepseek-v4-pro", "temperature": 0.9},
        },
    ))
    cfg = _read(host)
    assert cfg.agents.proactive.provider == "deepseek_main"
    assert cfg.agents.proactive.temperature == 0.2
    assert cfg.agents.summary is None
    assert cfg.agents.persona_gen.provider == "deepseek_main"
    assert cfg.agents.persona_gen.model == "deepseek-v4-pro"


def test_sub_agent_legacy_boolean_toggle(host):
    """老前端只发布尔开关，也要认。"""
    host.write_initial_config(_payload(agents={"proactive": False, "summary": True}))
    cfg = _read(host)
    assert cfg.agents.proactive is None
    assert cfg.agents.summary is not None


# ============================================================
# 渠道 / 白名单
# ============================================================


def test_whitelist_ids_are_persisted(host):
    """选了 whitelist 却没名单 = 谁都进不来，这里必须有值。"""
    host.write_initial_config(_payload(adapter={
        "mode": "client",
        "whitelist": {"mode": "whitelist", "qq_ids": [111, 222], "group_ids": [333]},
    }))
    wl = _read(host).adapters["default"].whitelist
    assert wl.mode == "whitelist"
    assert wl.qq_ids == [111, 222]
    assert wl.group_ids == [333]


def test_whitelist_accepts_pasted_string(host):
    """用户从别处粘贴的逗号 / 换行 / 顿号分隔名单也要能解析。"""
    host.write_initial_config(_payload(adapter={
        "mode": "client",
        "whitelist": {"mode": "whitelist", "qq_ids": "111, 222\n333、444"},
    }))
    assert _read(host).adapters["default"].whitelist.qq_ids == [111, 222, 333, 444]


def test_whitelist_empty_by_default(host):
    host.write_initial_config(_payload())
    wl = _read(host).adapters["default"].whitelist
    assert wl.qq_ids == []
    assert wl.group_ids == []


def test_adapter_managed_process(host):
    host.write_initial_config(_payload(adapter={
        "mode": "client", "manage_process": True, "process_path": "/opt/napcat",
        "auto_restart": False,
    }))
    adapter = _read(host).adapters["default"]
    assert adapter.manage_process is True
    assert adapter.process_path == "/opt/napcat"
    assert adapter.auto_restart is False


# ============================================================
# 功能段
# ============================================================


def test_features_are_written(host):
    host.write_initial_config(_payload(features={
        "vision": {"enabled": True, "provider": "deepseek_main", "model": "mimo-v2.5"},
        "web_search": {"enabled": True},
    }))
    features = _read(host).features
    assert features.vision.enabled is True
    assert features.vision.model == "mimo-v2.5"
    assert features.web_search.enabled is True


def test_features_absent_keeps_existing(host):
    """向导没提功能段时，不该把用户已经开好的功能关掉。"""
    host.write_initial_config(_payload(features={
        "vision": {"enabled": True, "provider": "deepseek_main", "model": "m"},
    }))
    host.write_initial_config(_payload())  # 第二次不带 features
    assert _read(host).features.vision.enabled is True
    assert _read(host).features.vision.model == "m"


def test_enabling_vision_without_provider_is_rejected(host):
    """开了视觉却没给 provider，schema 会拦下来——错误要在写盘前就报出来。"""
    with pytest.raises(Exception, match="vision"):
        host.write_initial_config(_payload(features={"vision": {"enabled": True}}))


def test_behavior_max_context_tokens(host):
    host.write_initial_config(_payload(behavior={"max_context_tokens": 200000}))
    assert _read(host).behavior.context.max_context_tokens == 200000


def test_behavior_max_context_tokens_can_be_cleared(host):
    host.write_initial_config(_payload(behavior={"max_context_tokens": 200000}))
    host.write_initial_config(_payload(behavior={"max_context_tokens": None}))
    assert _read(host).behavior.context.max_context_tokens is None


# ============================================================
# 密钥
# ============================================================


def test_api_key_is_stored_not_in_config(host):
    host.write_initial_config(_payload())
    cfg_path = host.build_paths().CONFIG_FILE
    assert "sk-test" not in cfg_path.read_text(encoding="utf-8")


def test_missing_key_leaves_api_key_id_empty(host):
    host.write_initial_config(_payload(
        providers=[{"id": "p1", "preset": "deepseek"}],
        chat={"provider": "p1", "model": "deepseek-v4-flash"},
    ))
    assert _read(host).providers["p1"].api_key_id is None


# ============================================================
# 落盘前的兜底校验
# ============================================================


def test_save_config_refuses_invalid_config(host):
    """内存配置不合法时拒绝落盘。

    这是最后一道闸：某个接口漏校验把内存改坏时，宁可这次保存失败，
    也不能写出一份 Runtime 下次启动直接起不来的配置。
    """
    from types import SimpleNamespace

    from app_config import ConfigError

    host.write_initial_config(_payload())
    cfg = _read(host)
    cfg.features.vision.enabled = True  # 直接赋值绕过校验，模拟内存被改坏
    cfg.features.vision.provider = ""
    host.rt = SimpleNamespace(config=cfg, paths=host.build_paths())

    before = host.config_file_path.read_text(encoding="utf-8")
    with pytest.raises(ConfigError, match="拒绝写入"):
        host.save_config()
    assert host.config_file_path.read_text(encoding="utf-8") == before
