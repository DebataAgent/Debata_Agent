"""Provider 增删改（纯函数，不依赖 FastAPI）。

原 GUI 的设置页「模型」分页可以添加 / 编辑 / 删除提供商、拉取模型列表、测试连接
（``ui/dashboard/settings/model.py``）。WebUI 一度只能在首次向导里配一次 provider，
之后再想接中转站或新厂商就只能手改 ``data/config.yaml``——这个模块补上那个入口。

三条设计约束：

1. **改动先在副本上做，校验通过才落到真配置。**
   ``RootConfig.validate_references()`` 会检查每个 Agent 引用的 provider 是否存在，
   引用校验失败时必须整体回滚，不能留下半改状态。
2. **密钥只进 SecretsManager，永不回显。**
   响应里只有 ``has_key: bool``，连 ``api_key_id`` 都只是 ID 而不是值。
3. **删除 provider 默认保留密钥。**
   删配置是常见操作（比如换个 base_url 重建），顺手删掉密钥会让用户还得重新申请。
   要删得显式传 ``drop_key``。

provider 实例在 ``Runtime.start()`` 时构造（``core/runtime.py``），所以任何改动都
需要重启 Runtime 才生效。这里不做热重建——那会打断在途请求，得不偿失。
"""

from __future__ import annotations

import logging
import re
from typing import Any, NamedTuple

from pydantic import BaseModel, Field, ValidationError

from app_config.schema import ProviderConfig, RootConfig
from webui.config_edit import apply_validated

logger = logging.getLogger(__name__)

PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

RESERVED_PROVIDER_IDS = frozenset({"default", "napcat", "none", "null", "test"})
"""这些名字容易与别处的占位符混淆，不让用作 provider id。"""

AGENT_NAMES = ("chat", "proactive", "summary", "persona_gen")


class ProviderAdminError(Exception):
    """操作失败。消息直接展示给用户。"""


class ProviderInUseError(ProviderAdminError):
    """删除时仍有 Agent 引用它。"""

    def __init__(self, provider_id: str, used_by: list[str]) -> None:
        super().__init__(
            f"{provider_id} 还被这些地方引用：{'、'.join(used_by)}。"
            "请先把它们改到别的提供商，再删除。"
        )
        self.provider_id = provider_id
        self.used_by = used_by


class ProviderPayload(BaseModel):
    """新增 / 编辑 provider 的请求体。

    ``None`` 的语义靠 ``model_fields_set`` 区分：
        - 字段没出现在请求里 → 不改动
        - 字段出现且值为 null → 清空（比如把 base_url 清掉，回落到 preset 的默认）
    """

    id: str | None = Field(None, description="仅新增时使用；编辑时忽略")
    display_name: str | None = None
    preset: str | None = None
    protocol: str | None = None
    base_url: str | None = None
    api_key_id: str | None = None
    api_key: str | None = Field(None, description="只写不读；传值即写入密钥")
    clear_api_key: bool = Field(False, description="置 true 则删除已存密钥")
    extra_headers: dict[str, str] | None = None
    timeout_seconds: float | None = None
    reasoning_style: str | None = None

    model: str | None = Field(
        None, description="仅用于 test / fetch-models 探测，不是 provider 配置字段"
    )


# ============================================================
# 校验辅助
# ============================================================


def validate_provider_id(provider_id: str) -> str:
    """provider id 要能安全地出现在 URL 路径与密钥 ID 里。"""
    candidate = (provider_id or "").strip()
    if not candidate:
        raise ProviderAdminError("提供商 ID 不能为空")
    if not PROVIDER_ID_RE.match(candidate):
        raise ProviderAdminError(
            "提供商 ID 只能用小写字母开头，后跟小写字母、数字、下划线或短横线，最长 64 个字符"
        )
    if candidate in RESERVED_PROVIDER_IDS:
        raise ProviderAdminError(f"{candidate} 是保留名，换一个")
    return candidate


def key_id_for(provider_id: str) -> str:
    """约定：provider「relay01」的密钥存在 secrets 的「relay01_key」下。"""
    return f"{provider_id}_key"


def _rollback_on_failure(cfg: RootConfig, mutate) -> None:
    """在深拷贝上改，校验通过才写回原对象。"""
    try:
        apply_validated(cfg, mutate)
    except ValidationError as e:
        raise ProviderAdminError(f"配置校验未通过：{e}") from e


# ============================================================
# 查询
# ============================================================


def provider_references(cfg: RootConfig, provider_id: str) -> list[str]:
    """列出引用了该 provider 的位置（Agent 名 / 功能名）。"""
    refs: list[str] = []
    for name in AGENT_NAMES:
        agent = getattr(cfg.agents, name, None)
        if agent is not None and getattr(agent, "provider", None) == provider_id:
            refs.append(f"agents.{name}")
    vision = getattr(cfg.features, "vision", None)
    if vision is not None and getattr(vision, "provider", None) == provider_id:
        refs.append("features.vision")
    embedding = getattr(cfg.features, "embedding", None)
    if embedding is not None and getattr(embedding, "provider", None) == provider_id:
        refs.append("features.embedding")
    return refs


def list_providers(
    cfg: RootConfig,
    secrets: Any,
    *,
    health: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """列出全部 provider。**不含任何密钥内容**，只有 has_key。"""
    health = health or {}
    out: list[dict[str, Any]] = []
    for pid, provider in (cfg.providers or {}).items():
        key_id = getattr(provider, "api_key_id", None) or ""
        has_key = bool(key_id) and _secret_exists(secrets, key_id)
        probe = health.get(pid)
        out.append(
            {
                "id": pid,
                "display_name": getattr(provider, "display_name", None) or pid,
                "preset": getattr(provider, "preset", None) or "",
                "protocol": getattr(provider, "protocol", None) or "",
                "base_url": getattr(provider, "base_url", None) or "",
                "api_key_id": key_id,
                "has_key": has_key,
                "extra_headers": dict(getattr(provider, "extra_headers", {}) or {}),
                "timeout_seconds": getattr(provider, "timeout_seconds", 120.0),
                "reasoning_style": getattr(provider, "reasoning_style", None) or "",
                "used_by": provider_references(cfg, pid),
                "health": _health_to_dict(probe),
            }
        )
    out.sort(key=lambda x: x["id"])
    return out


def _secret_exists(secrets: Any, key_id: str) -> bool:
    try:
        return secrets.get(key_id) is not None
    except Exception:  # noqa: BLE001
        logger.debug("读取密钥失败 key_id=%s", key_id, exc_info=True)
        return False


def _health_to_dict(probe: Any) -> dict[str, Any] | None:
    if probe is None:
        return None
    return {
        "status": getattr(probe, "status", ""),
        "message": getattr(probe, "message", ""),
        "latency_ms": getattr(probe, "latency_ms", 0),
    }


# ============================================================
# 增删改
# ============================================================


def create_provider(
    cfg: RootConfig,
    secrets: Any,
    payload: ProviderPayload,
    *,
    presets: dict[str, Any] | None = None,
) -> str:
    """新增一个 provider，返回它的 id。"""
    provider_id = validate_provider_id(payload.id or "")
    if provider_id in (cfg.providers or {}):
        raise ProviderAdminError(f"{provider_id} 已存在，换个 ID 或直接编辑它")

    if payload.preset and presets is not None and payload.preset.lower() not in presets:
        raise ProviderAdminError(
            f"未知预设：{payload.preset}。可用预设：{'、'.join(sorted(presets))}"
        )

    fields = _config_fields(payload, is_create=True)
    try:
        new_config = ProviderConfig(**fields)
    except ValidationError as e:
        raise ProviderAdminError(_friendly_validation(e)) from e

    _rollback_on_failure(
        cfg, lambda draft: draft.providers.__setitem__(provider_id, new_config)
    )

    key_id = payload.api_key_id or key_id_for(provider_id)
    cfg.providers[provider_id].api_key_id = key_id
    if payload.api_key:
        _write_secret(secrets, key_id, payload.api_key)

    logger.info("已新增 provider %s（preset=%s）", provider_id, payload.preset or "自定义")
    return provider_id


def update_provider(
    cfg: RootConfig,
    secrets: Any,
    provider_id: str,
    payload: ProviderPayload,
    *,
    presets: dict[str, Any] | None = None,
) -> list[str]:
    """编辑一个 provider，返回实际改动的字段名。"""
    if provider_id not in (cfg.providers or {}):
        raise ProviderAdminError(f"没有 {provider_id} 这个提供商")

    if payload.preset and presets is not None and payload.preset.lower() not in presets:
        raise ProviderAdminError(
            f"未知预设：{payload.preset}。可用预设：{'、'.join(sorted(presets))}"
        )

    changed: list[str] = []
    provided = payload.model_fields_set

    def mutate(draft: RootConfig) -> None:
        target = draft.providers[provider_id]
        for name, value in _config_fields(payload, is_create=False).items():
            if name not in provided:
                continue
            if getattr(target, name, None) != value:
                changed.append(name)
            setattr(target, name, value)
        _revalidate_provider(target)

    _rollback_on_failure(cfg, mutate)

    # 密钥单独处理：它不在 config.yaml 里
    key_id = cfg.providers[provider_id].api_key_id or key_id_for(provider_id)
    if payload.clear_api_key:
        _delete_secret(secrets, key_id)
        cfg.providers[provider_id].api_key_id = None
        changed.append("api_key")
    elif payload.api_key:
        _write_secret(secrets, key_id, payload.api_key)
        if cfg.providers[provider_id].api_key_id != key_id:
            cfg.providers[provider_id].api_key_id = key_id
        changed.append("api_key")

    logger.info("已更新 provider %s，改动字段：%s", provider_id, changed or ["（无）"])
    return changed


def delete_provider(
    cfg: RootConfig,
    secrets: Any,
    provider_id: str,
    *,
    drop_key: bool = False,
) -> bool:
    """删除一个 provider。被引用时抛 ProviderInUseError。

    默认**保留**密钥：删配置往往是为了重建（比如换 base_url），
    顺手删密钥会让用户还得重新去申请一次。
    """
    if provider_id not in (cfg.providers or {}):
        raise ProviderAdminError(f"没有 {provider_id} 这个提供商")

    used_by = provider_references(cfg, provider_id)
    if used_by:
        raise ProviderInUseError(provider_id, used_by)

    key_id = cfg.providers[provider_id].api_key_id

    def mutate(draft: RootConfig) -> None:
        del draft.providers[provider_id]

    _rollback_on_failure(cfg, mutate)

    if drop_key and key_id:
        _delete_secret(secrets, key_id)

    logger.info("已删除 provider %s（密钥%s）", provider_id, "一并删除" if drop_key else "保留")
    return True


def _config_fields(payload: ProviderPayload, *, is_create: bool) -> dict[str, Any]:
    """把 payload 翻成 ProviderConfig 的字段。api_key 不是配置字段，单独处理。

    两种情况语义不同：
        新建 —— 值为 None 表示"没填"，直接跳过让 pydantic 用默认值。
                （不能传 None，因为 extra_headers / timeout_seconds 是非空字段）
        编辑 —— 值为 None 表示"清空"，要显式写进去。
    """
    names = (
        "display_name",
        "preset",
        "protocol",
        "base_url",
        "api_key_id",
        "extra_headers",
        "timeout_seconds",
        "reasoning_style",
    )
    provided = payload.model_fields_set if not is_create else None
    out: dict[str, Any] = {}
    for name in names:
        if not is_create and name not in provided:
            continue
        value = getattr(payload, name)
        # 空字符串当作"清空"，否则前端把输入框留空会写出 base_url=""
        if isinstance(value, str) and not value.strip():
            value = None
        if value is None:
            if is_create:
                continue
            # 这两个字段非空，清空的正确表示是各自的空值
            if name == "extra_headers":
                value = {}
            elif name == "timeout_seconds":
                value = 120.0
        out[name] = value
    return out


def _revalidate_provider(provider: ProviderConfig) -> None:
    """改完单条后立刻跑一次它自己的校验器（preset 与 protocol/base_url 的互斥）。"""
    try:
        ProviderConfig.model_validate(provider.model_dump())
    except ValidationError as e:
        raise ProviderAdminError(_friendly_validation(e)) from e


def _friendly_validation(error: ValidationError) -> str:
    """把 pydantic 的报错压成一句人话。"""
    first = error.errors()[0] if error.errors() else None
    if first is None:
        return "配置不合法"
    message = str(first.get("msg", "")).removeprefix("Value error, ")
    return message or "配置不合法"


def _write_secret(secrets: Any, key_id: str, value: str) -> None:
    try:
        secrets.set(key_id, value)
    except Exception as e:  # noqa: BLE001
        raise ProviderAdminError(f"写入密钥失败：{e}") from e


def _delete_secret(secrets: Any, key_id: str) -> None:
    try:
        secrets.delete(key_id)
    except Exception:  # noqa: BLE001
        logger.debug("删除密钥失败 key_id=%s", key_id, exc_info=True)


# ============================================================
# 远端能力：取模型列表 / 测连接
# ============================================================


class ProbeTarget(NamedTuple):
    """一次远端探测所需的全部信息。"""

    protocol: str
    base_url: str
    api_key: str
    model: str


def resolve_probe_target(
    cfg: RootConfig,
    secrets: Any,
    presets: dict[str, Any],
    payload: ProviderPayload,
    *,
    provider_id: str | None = None,
) -> ProbeTarget:
    """算出一组用于探测的连接信息。

    优先用请求体里的值（"还没保存就想先测一下"），缺的字段回落到已保存的配置，
    最后才回落到 preset。密钥可以先明文传，也可以引用已存的 key_id。

    探测必须带一个模型名（要真发一次最小请求），顺序是：
    请求体 model > 绑定到该 provider 的 Agent 的 model > preset 的首个模型。
    """
    from providers.registry import normalize_base_url

    existing = None
    if provider_id and provider_id in (cfg.providers or {}):
        existing = cfg.providers[provider_id]

    preset_id = payload.preset or (getattr(existing, "preset", None) if existing else None)
    preset = presets.get(preset_id.lower()) if preset_id else None

    protocol = (
        payload.protocol
        or (getattr(existing, "protocol", None) if existing else None)
        or (preset.protocol if preset else None)
    )
    if not protocol:
        raise ProviderAdminError("还没选协议：选一个预设，或手动指定 openai_compat / anthropic")

    base_url = (
        payload.base_url
        or (getattr(existing, "base_url", None) if existing else None)
        or (preset.base_url if preset else None)
    )
    if not base_url:
        raise ProviderAdminError("还没填 Base URL")

    api_key = payload.api_key or ""
    if not api_key:
        key_id = (
            payload.api_key_id
            or (getattr(existing, "api_key_id", None) if existing else None)
            or (key_id_for(provider_id) if provider_id else "")
        )
        if key_id:
            api_key = secrets.get(key_id) or ""
    if not api_key:
        raise ProviderAdminError("还没填 API Key")

    model = payload.model or ""
    if not model and provider_id:
        for name in AGENT_NAMES:
            agent = getattr(cfg.agents, name, None)
            if agent is not None and getattr(agent, "provider", None) == provider_id:
                model = getattr(agent, "model", "") or ""
                if model:
                    break
    if not model and preset is not None and preset.models:
        model = preset.models[0].id
    if not model:
        raise ProviderAdminError("还没填模型 ID：测试连接需要发一次真实请求，得知道用哪个模型")

    return ProbeTarget(
        protocol=protocol,
        base_url=normalize_base_url(base_url, protocol),
        api_key=api_key,
        model=model,
    )


__all__ = [
    "AGENT_NAMES",
    "RESERVED_PROVIDER_IDS",
    "ProbeTarget",
    "ProviderAdminError",
    "ProviderInUseError",
    "ProviderPayload",
    "create_provider",
    "delete_provider",
    "key_id_for",
    "list_providers",
    "provider_references",
    "resolve_probe_target",
    "update_provider",
    "validate_provider_id",
]
