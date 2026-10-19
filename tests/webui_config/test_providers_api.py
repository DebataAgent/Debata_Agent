"""Provider HTTP 路由测试。

和表情包那边一样直接调路由函数（不用 TestClient），原因见 test_emoji_api.py 的模块注释。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app_config.schema import RootConfig
from webui.api import providers as api
from webui.provider_admin import ProviderPayload


class FakeSecrets:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.data = dict(initial or {})

    def get(self, key_id: str):
        return self.data.get(key_id)

    def set(self, key_id: str, value: str) -> None:
        self.data[key_id] = value

    def delete(self, key_id: str) -> None:
        self.data.pop(key_id, None)


class FakeHost:
    """RuntimeHost 替身。``rt`` 是 ``require_runtime()`` 的取值入口（见 deps.py）。"""

    def __init__(self, rt) -> None:
        self.rt = rt
        self.state = "running"
        self.last_error = None
        self.saved = 0

    def save_config(self) -> None:
        self.saved += 1

    def build_paths(self):
        raise AssertionError("provider 路由不该碰文件路径")


def _config() -> RootConfig:
    return RootConfig.model_validate(
        {
            "persona": {"name": "test_bot"},
            "adapters": {"default": {"type": "napcat"}},
            "providers": {
                "deepseek_main": {"preset": "deepseek", "api_key_id": "deepseek_main_key"},
            },
            "agents": {"chat": {"provider": "deepseek_main", "model": "deepseek-v4-flash"}},
        }
    )


@pytest.fixture
def presets():
    from providers.presets_loader import load_all_presets

    root = Path(__file__).resolve().parent.parent.parent
    return load_all_presets(root / "providers" / "presets")


@pytest.fixture
def rt(presets):
    return SimpleNamespace(
        config=_config(),
        secrets=FakeSecrets({"deepseek_main_key": "sk-test"}),
        provider_registry=SimpleNamespace(presets=presets),
        provider_health={},
        providers={},
    )


@pytest.fixture
def req(rt):
    host = FakeHost(rt)
    app = SimpleNamespace(state=SimpleNamespace(host=host))
    request = SimpleNamespace(app=app)
    request._host = host
    return request


# ============================================================
# 读
# ============================================================


async def test_list_all(rt, req):
    data = await api.list_all(req)
    assert [p["id"] for p in data["providers"]] == ["deepseek_main"]
    assert data["agents"]["chat"]["provider"] == "deepseek_main"
    assert "mimo" in data["presets"]


async def test_list_all_never_leaks_key(rt, req):
    data = await api.list_all(req)
    assert "sk-test" not in repr(data)
    assert data["providers"][0]["has_key"] is True


async def test_list_presets(req, presets):
    data = await api.list_presets(req)
    by_id = {p["id"]: p for p in data["presets"]}
    assert "mimo" in by_id
    mimo = by_id["mimo"]
    assert mimo["protocol"] == "openai_compat"
    assert mimo["base_url"].startswith("https://api.xiaomimimo.com")
    assert {m["id"] for m in mimo["models"]} == {"mimo-v2.5-pro", "mimo-v2.5"}


# ============================================================
# 增
# ============================================================


async def test_create_relay(rt, req):
    result = await api.create(
        req,
        ProviderPayload(
            id="relay01",
            protocol="openai_compat",
            base_url="https://relay.example.com/v1",
            api_key="sk-relay",
        ),
    )
    assert result["ok"] is True
    assert result["id"] == "relay01"
    assert result["needs_restart"] is True
    assert rt.config.providers["relay01"].base_url == "https://relay.example.com/v1"
    assert rt.secrets.data["relay01_key"] == "sk-relay"
    assert req._host.saved == 1


async def test_create_bad_id_is_400(rt, req):
    with pytest.raises(HTTPException) as excinfo:
        await api.create(req, ProviderPayload(id="Bad ID"))
    assert excinfo.value.status_code == 400


async def test_create_unknown_preset_is_400(rt, req):
    with pytest.raises(HTTPException) as excinfo:
        await api.create(req, ProviderPayload(id="x01", preset="nope"))
    assert excinfo.value.status_code == 400


async def test_create_does_not_save_on_failure(rt, req):
    with pytest.raises(HTTPException):
        await api.create(req, ProviderPayload(id="x01"))
    assert req._host.saved == 0
    assert "x01" not in rt.config.providers


# ============================================================
# 改
# ============================================================


async def test_update_base_url(rt, req):
    result = await api.update(
        req, "deepseek_main", ProviderPayload(base_url="https://relay.example.com/v1")
    )
    assert result["changed"] == ["base_url"]
    assert rt.config.providers["deepseek_main"].base_url == "https://relay.example.com/v1"


async def test_update_reasoning_style_to_none(rt, req):
    """中转站场景：不发厂商私有字段。"""
    await api.update(req, "deepseek_main", ProviderPayload(reasoning_style="none"))
    assert rt.config.providers["deepseek_main"].reasoning_style == "none"


async def test_update_missing_is_400(rt, req):
    with pytest.raises(HTTPException) as excinfo:
        await api.update(req, "nope", ProviderPayload(display_name="x"))
    assert excinfo.value.status_code == 400


async def test_update_bad_id_is_400(rt, req):
    with pytest.raises(HTTPException) as excinfo:
        await api.update(req, "Bad ID", ProviderPayload(display_name="x"))
    assert excinfo.value.status_code == 400


# ============================================================
# 删
# ============================================================


async def test_delete_in_use_is_409(rt, req):
    """被 Agent 引用时给 409 并带上 used_by，前端据此引导用户先改绑。"""
    with pytest.raises(HTTPException) as excinfo:
        await api.remove(req, "deepseek_main")
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["used_by"] == ["agents.chat"]
    assert "deepseek_main" in rt.config.providers


async def test_delete_keeps_key_by_default(rt, req):
    rt.config.agents.chat.provider = "other"
    from app_config.schema import ProviderConfig

    rt.config.providers["other"] = ProviderConfig(
        protocol="openai_compat", base_url="https://other.example.com/v1"
    )
    await api.remove(req, "deepseek_main")
    assert "deepseek_main" not in rt.config.providers
    assert rt.secrets.data["deepseek_main_key"] == "sk-test"


async def test_delete_drop_key(rt, req):
    from app_config.schema import ProviderConfig

    rt.config.agents.chat.provider = "other"
    rt.config.providers["other"] = ProviderConfig(
        protocol="openai_compat", base_url="https://other.example.com/v1"
    )
    await api.remove(req, "deepseek_main", drop_key=True)
    assert "deepseek_main_key" not in rt.secrets.data


# ============================================================
# 探测参数解析的失败分支（不打真实网络）
# ============================================================


async def test_test_unsaved_without_key_is_400(rt, req):
    with pytest.raises(HTTPException) as excinfo:
        await api.test_unsaved(
            req,
            ProviderPayload(
                protocol="openai_compat",
                base_url="https://relay.example.com/v1",
                model="m",
            ),
        )
    assert excinfo.value.status_code == 400
    assert "API Key" in str(excinfo.value.detail)


async def test_test_unsaved_without_model_is_400(rt, req):
    with pytest.raises(HTTPException) as excinfo:
        await api.test_unsaved(
            req,
            ProviderPayload(
                protocol="openai_compat",
                base_url="https://relay.example.com/v1",
                api_key="sk-x",
            ),
        )
    assert excinfo.value.status_code == 400
    assert "模型" in str(excinfo.value.detail)


async def test_test_saved_missing_provider_is_404(rt, req):
    with pytest.raises(HTTPException) as excinfo:
        await api.test_saved(req, "nope")
    assert excinfo.value.status_code == 404


async def test_test_saved_not_assembled_is_409(rt, req):
    """配置里有、但 Runtime 还没装配（比如刚加完没重启）时给出可操作的提示。"""
    with pytest.raises(HTTPException) as excinfo:
        await api.test_saved(req, "deepseek_main")
    assert excinfo.value.status_code == 409
    assert "重启" in str(excinfo.value.detail)


async def test_fetch_saved_missing_is_404(rt, req):
    with pytest.raises(HTTPException) as excinfo:
        await api.fetch_saved(req, "nope")
    assert excinfo.value.status_code == 404
