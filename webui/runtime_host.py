"""Runtime 宿主 —— 把 ``core.Runtime`` 的生命周期收敛到 WebUI 里。

原 GUI 用 qasync 把 Qt 事件循环和 asyncio 合成一个，Runtime 就跑在 UI 线程的
事件循环上。WebUI 换成 uvicorn：Runtime 直接跑在 **uvicorn 自己的事件循环**里
（由 FastAPI lifespan 驱动 start/shutdown），所以不存在跨线程 / 跨 loop 访问
asyncio 对象的问题，页面接口只是普通的 async 调用。

宿主还负责三件 WebUI 特有的事：
    - 首次配置：没有 config.yaml 时给出 ``setup_required`` 状态而不是崩溃
    - 配置落盘：统一的 ``save_config`` 入口（等价 GUI 的 ``_save_now``）
    - 重启：改完需要重启的配置后原地重启 Runtime
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

logger = logging.getLogger(__name__)

STATE_SETUP_REQUIRED = "setup_required"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_STOPPED = "stopped"
STATE_ERROR = "error"


class RuntimeHost:
    """持有唯一的 Runtime 实例，并暴露给各 API 路由。"""

    def __init__(self, project_root: Path, config_file: Path | None = None) -> None:
        self.project_root = Path(project_root)
        self.config_file = Path(config_file) if config_file else None
        self.rt: Any = None
        self.paths: Any = None
        self.state: str = STATE_STOPPED
        self.last_error: str = ""
        self.started_at: float = 0.0
        self._lock = asyncio.Lock()
        # 单独一把锁：保证「stop 再 start」这一对不会被另一个重启请求插进来，
        # 否则两次并发重启可能交错成 stop/stop/start/start。
        self._restart_lock = asyncio.Lock()
        # 启动过程中产生的非致命问题（密钥缺失、feature 加载失败等）
        self.warnings: list[str] = []
        self._stop_requested = False

    # ============================================================
    # 路径 / 配置
    # ============================================================

    def build_paths(self) -> Any:
        """构造 AppPaths 并建好目录；不依赖 Runtime。"""
        from app_config import AppPaths, initialize_runtime_data

        if self.paths is None:
            self.paths = AppPaths(project_root=self.project_root, config_file=self.config_file)
            self.paths.ensure_data_dirs()
            initialize_runtime_data(self.paths, project_root=self.project_root)
        return self.paths

    @property
    def config_file_path(self) -> Path:
        return self.build_paths().CONFIG_FILE

    def has_config(self) -> bool:
        try:
            return self.config_file_path.exists()
        except Exception:  # noqa: BLE001
            return False

    def load_config(self) -> Any:
        from app_config.loader import load_config

        return load_config(self.build_paths())

    def save_config(self, backup: bool = True) -> None:
        """把内存里的 ``rt.config`` 写回 yaml（等价 GUI 的 ``_save_now``）。

        落盘前再校验一次：调用方漏校验时，宁可这次保存失败，也不能把一份
        Runtime 下次启动才炸的配置写出去。
        """
        from app_config.loader import ConfigError, save_config, user_error_text
        from webui.config_edit import validate_config

        if self.rt is None or self.rt.config is None:
            raise RuntimeError("Runtime 未启动，无法保存配置")
        try:
            validate_config(self.rt.config)
        except ValidationError as e:
            raise ConfigError(f"配置不合法，已拒绝写入：{user_error_text(e)}") from e
        save_config(self.rt.paths, self.rt.config, backup=backup)

    def secrets(self) -> Any:
        """取 SecretsManager（已 initialize）。"""
        from app_config import SecretsManager

        sm = SecretsManager(self.build_paths())
        sm.initialize()
        return sm

    # ============================================================
    # 生命周期
    # ============================================================

    async def start(self) -> bool:
        """启动 Runtime。返回是否成功（失败时 ``state=error``，不抛异常）。"""
        async with self._lock:
            if self.state == STATE_RUNNING:
                return True
            self._stop_requested = False
            self.warnings = []

            if not self.has_config():
                self.state = STATE_SETUP_REQUIRED
                self.last_error = ""
                logger.info("未找到 config.yaml，WebUI 进入首次配置模式：%s", self.config_file_path)
                return False

            self.state = STATE_STARTING
            self.last_error = ""
            from core import Runtime

            rt = Runtime(project_root=self.project_root, config_file=self.config_file)
            self.rt = rt
            try:
                await rt.start()
            except Exception as e:  # noqa: BLE001
                self.state = STATE_ERROR
                self.last_error = str(e)
                logger.exception("Runtime 启动失败")
                try:
                    await rt.shutdown()
                except Exception:  # noqa: BLE001
                    logger.debug("启动失败后清理 Runtime 出错", exc_info=True)
                self.rt = None
                return False

            self.state = STATE_RUNNING
            self.started_at = time.time()
            self._collect_warnings()
            logger.info("Runtime 已就绪（persona=%s）", getattr(rt.persona, "name", "?"))
            return True

    async def stop(self) -> None:
        async with self._lock:
            rt = self.rt
            self.rt = None
            self.state = STATE_STOPPED
            if rt is None:
                return
            try:
                await rt.shutdown()
            except Exception:  # noqa: BLE001
                logger.exception("Runtime 关闭失败")

    async def restart(self) -> bool:
        """原地重启（改完需要重启的配置后调用）。"""
        async with self._restart_lock:
            logger.info("正在重启 Runtime ...")
            await self.stop()
            return await self.start()

    def request_stop(self) -> None:
        """供系统级接口请求退出整个 WebUI。"""
        self._stop_requested = True
        if self.rt is not None:
            try:
                self.rt.request_stop()
            except Exception:  # noqa: BLE001
                logger.debug("request_stop 失败", exc_info=True)

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def _collect_warnings(self) -> None:
        rt = self.rt
        if rt is None:
            return
        for name, reason in (getattr(rt, "feature_failures", None) or {}).items():
            self.warnings.append(f"功能 {name} 未就绪：{reason}")
        try:
            missing = _find_missing_secrets(rt)
        except Exception:  # noqa: BLE001
            missing = []
        for item in missing:
            self.warnings.append(f"密钥缺失：{item}")

    # ============================================================
    # 状态快照
    # ============================================================

    def status(self) -> dict[str, Any]:
        """顶栏 / 总览页要用的运行状态。"""
        rt = self.rt
        info: dict[str, Any] = {
            "state": self.state,
            "last_error": self.last_error,
            "warnings": list(self.warnings),
            "config_file": str(self.config_file_path),
            "data_dir": str(self.build_paths().DATA_DIR),
            "uptime_seconds": (time.time() - self.started_at) if self.started_at else 0.0,
            "adapter_connected": False,
            "adapter": None,
            "persona": "",
            "persona_id": "",
        }
        if rt is None:
            return info

        cfg = getattr(rt, "config", None)
        if cfg is not None:
            info["persona_id"] = cfg.persona.active
            try:
                name, adapter_cfg = next(iter(cfg.adapters.items()))
                info["adapter"] = {
                    "name": name,
                    "mode": adapter_cfg.mode,
                    "host": adapter_cfg.host,
                    "port": adapter_cfg.port,
                    "path": adapter_cfg.path,
                    "enabled": adapter_cfg.enabled,
                }
            except StopIteration:
                pass
        persona = getattr(rt, "persona", None)
        if persona is not None:
            info["persona"] = getattr(persona, "name", "") or info["persona_id"]
        adapter = getattr(rt, "adapter", None)
        if adapter is not None:
            try:
                info["adapter_connected"] = bool(adapter.is_connected)
            except Exception:  # noqa: BLE001
                info["adapter_connected"] = False
        return info

    # ============================================================
    # 首次配置：用表单数据生成一份 RootConfig
    # ============================================================

    def write_initial_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """按向导 payload 写入 config.yaml 与密钥。

        返回 ``{"ok": bool, "message": str, "provider_id": str, "model": str}``。
        payload 结构见 ``webui/api/system.py`` 的 ``SetupPayload``。

        两处容易踩的地方：

        - **providers 是按 id 合并，不是整段替换。** 用户完全可能在设置页加过一个
          中转站，之后重跑向导只是想改主模型；整段替换会把那个 provider 连同
          引用它的 Agent 一起打回原形。要真正重置得显式传 ``replace_providers``。
        - **写盘前跑一次 RootConfig 校验。** Agent 引用不存在的 provider 这类错误
          必须在这里拦住，否则会写出一份启动时才炸的配置。
        """
        from app_config.loader import load_config, save_config
        from app_config.schema import (
            AgentConfig,
            AgentsConfig,
            BehaviorConfig,
            EmbeddingFeatureConfig,
            FeaturesConfig,
            LongTermMemoryConfig,
            NapCatAdapterConfig,
            PersonaConfig,
            ProviderConfig,
            ReasoningConfig,
            RootConfig,
            TTSFeatureConfig,
            VisionFeatureConfig,
            WeatherFeatureConfig,
            WebSearchFeatureConfig,
            WhitelistConfig,
        )

        paths = self.build_paths()
        providers_in = payload.get("providers") or []
        if not providers_in:
            raise ValueError("至少需要配置一个模型提供商")

        secrets = self.secrets()
        # 不走 provider 的密钥（天气 / TTS 等）。在这里统一写，是因为向导阶段
        # Runtime 还没起来，前端没法调 /api/settings/secrets。
        for key_id, value in (payload.get("secrets") or {}).items():
            if value:
                secrets.set(str(key_id), str(value))

        providers: dict[str, ProviderConfig] = {}
        for item in providers_in:
            pid = str(item.get("id") or "").strip()
            if not pid:
                raise ValueError("Provider ID 不能为空")
            key_id = str(item.get("api_key_id") or f"{pid}_key").strip()
            api_key = (item.get("api_key") or "").strip()
            if api_key:
                secrets.set(key_id, api_key)
            elif not secrets.has(key_id):
                key_id = ""
            providers[pid] = ProviderConfig(
                preset=item.get("preset") or None,
                display_name=item.get("display_name") or pid,
                protocol=item.get("protocol") or None,
                base_url=item.get("base_url") or None,
                api_key_id=key_id or None,
                extra_headers=dict(item.get("extra_headers") or {}),
                timeout_seconds=float(item.get("timeout_seconds") or 120.0),
                reasoning_style=item.get("reasoning_style") or None,
            )

        chat_in = payload.get("chat") or {}
        chat_provider = str(chat_in.get("provider") or "").strip()
        if chat_provider not in providers:
            chat_provider = next(iter(providers))
        chat_model = str(chat_in.get("model") or "").strip() or _default_model_for(
            paths, providers[chat_provider], chat_provider
        )

        def _reasoning(agent_in: dict[str, Any]) -> ReasoningConfig | None:
            """向导里思考是个开关 + 档位；三项都缺就不配 reasoning。"""
            if not any(k in agent_in for k in ("reasoning", "reasoning_budget")):
                return None
            return ReasoningConfig(
                enabled=bool(agent_in.get("reasoning")),
                budget=agent_in.get("reasoning_budget") or None,
                max_tokens=agent_in.get("reasoning_max_tokens") or None,
            )

        def _agent(
            agent_in: dict[str, Any],
            *,
            provider: str,
            model: str,
            temperature: float,
            max_tokens: int,
        ) -> AgentConfig:
            kwargs: dict[str, Any] = {
                "provider": str(agent_in.get("provider") or provider).strip() or provider,
                "model": str(agent_in.get("model") or model).strip() or model,
                "temperature": float(agent_in.get("temperature", temperature)),
                "max_tokens": int(agent_in.get("max_tokens", max_tokens)),
                "top_p": float(agent_in.get("top_p", 1.0)),
                "first_token_timeout_seconds": float(
                    agent_in.get("first_token_timeout_seconds", 30.0)
                ),
            }
            reasoning = _reasoning(agent_in)
            if reasoning is not None:
                kwargs["reasoning"] = reasoning
            return AgentConfig(**kwargs)

        chat_agent = _agent(
            chat_in,
            provider=chat_provider,
            model=chat_model,
            temperature=0.6,
            max_tokens=16384,
        )
        # 子 Agent 各自可以有自己的 provider/model；缺省时跟随主模型
        agents_in = payload.get("agents") or {}
        sub_agents: dict[str, AgentConfig | None] = {}
        for name, default_temp, default_tokens in (
            ("proactive", 0.3, 64),
            ("summary", 0.1, 8192),
            ("persona_gen", 0.7, 8192),
        ):
            item = agents_in.get(name)
            if item is False:          # 老前端用布尔表示开关
                sub_agents[name] = None
                continue
            if item is None:
                item = {}
            if isinstance(item, dict) and item.get("enabled") is False:
                sub_agents[name] = None
                continue
            if not isinstance(item, dict):
                item = {}
            sub_agents[name] = _agent(
                item,
                provider=chat_provider,
                model=chat_model,
                temperature=default_temp,
                max_tokens=default_tokens,
            )

        adapter_in = payload.get("adapter") or {}
        whitelist_in = adapter_in.get("whitelist") or {}
        # 名单以前被前端写死成空数组——选了 whitelist 却没有名单 = 谁都进不来
        whitelist = WhitelistConfig(
            mode=whitelist_in.get("mode") or "verify",
            qq_ids=_int_list(whitelist_in.get("qq_ids")),
            group_ids=_int_list(whitelist_in.get("group_ids")),
        )
        adapter = NapCatAdapterConfig(
            enabled=bool(adapter_in.get("enabled", True)),
            mode=adapter_in.get("mode") or "client",
            host=adapter_in.get("host") or "127.0.0.1",
            port=int(adapter_in.get("port") or 3000),
            path=adapter_in.get("path") or "/",
            manage_process=bool(adapter_in.get("manage_process", False)),
            process_path=str(adapter_in.get("process_path") or ""),
            auto_restart=bool(adapter_in.get("auto_restart", True)),
            whitelist=whitelist,
        )
        token = (adapter_in.get("access_token") or "").strip()
        if token:
            secrets.set("napcat_default_token", token)
            adapter.access_token_id = "napcat_default_token"

        cfg = RootConfig(
            providers=providers,
            agents=AgentsConfig(
                chat=chat_agent,
                proactive=sub_agents["proactive"],
                summary=sub_agents["summary"],
                persona_gen=sub_agents["persona_gen"],
            ),
            features=_build_features(
                payload.get("features") or {},
                FeaturesConfig,
                {
                    "vision": VisionFeatureConfig,
                    "weather": WeatherFeatureConfig,
                    "tts": TTSFeatureConfig,
                    "web_search": WebSearchFeatureConfig,
                    "embedding": EmbeddingFeatureConfig,
                    "long_term_memory": LongTermMemoryConfig,
                },
            ),
            behavior=BehaviorConfig(**_behavior_overrides(payload.get("behavior") or {})),
            persona=PersonaConfig(active=payload.get("persona") or "debata"),
            adapters={"default": adapter},
        )

        # 已存在配置时按 id 合并，避免向导覆盖掉用户后来加的 provider / 功能段。
        if paths.CONFIG_FILE.exists():
            try:
                existing = load_config(paths, set_global=False)
            except Exception:  # noqa: BLE001
                logger.warning("现有配置无法解析，将直接覆盖", exc_info=True)
            else:
                merged = existing.model_copy(deep=True)
                if payload.get("replace_providers"):
                    merged.providers = cfg.providers
                else:
                    merged.providers.update(cfg.providers)
                merged.agents = cfg.agents
                merged.persona = cfg.persona
                merged.adapters = cfg.adapters
                # 功能段：向导里显式给了才覆盖，没给就保留原来的
                if payload.get("features"):
                    merged.features = _merge_features(merged.features, cfg.features)
                if payload.get("behavior"):
                    merged.behavior = cfg.behavior
                cfg = merged

        # 写盘前跑一次完整校验：Agent 引用不存在的 provider 这类错误
        # 必须在这里拦住，否则会写出一份启动时才炸的配置
        try:
            RootConfig.model_validate(cfg.model_dump())
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"配置校验未通过：{e}") from e

        save_config(paths, cfg)
        _stash_persona_brief(paths, payload.get("persona_brief"))
        logger.info("首次配置已写入 %s", paths.CONFIG_FILE)
        return {
            "ok": True,
            "message": "配置已写入",
            "provider_id": chat_provider,
            "model": chat_model,
        }


def _find_missing_secrets(rt: Any) -> list[str]:
    """cfg 引用的 *_key_id / access_token_id 是否都在 secrets 里。"""
    missing: list[str] = []
    secrets = getattr(rt, "secrets", None)
    cfg = getattr(rt, "config", None)
    if secrets is None or cfg is None:
        return missing
    for pid, p in (cfg.providers or {}).items():
        if p.api_key_id and secrets.get(p.api_key_id) is None:
            missing.append(f"providers.{pid}.api_key_id = '{p.api_key_id}'")
    for aid, a in (cfg.adapters or {}).items():
        token_id = getattr(a, "access_token_id", None)
        if token_id and secrets.get(token_id) is None:
            missing.append(f"adapters.{aid}.access_token_id = '{token_id}'")
    return missing


PERSONA_BRIEF_FILE = "persona_brief.json"
"""向导里填的角色描述暂存处。

向导阶段 Runtime 还没起来，没法当场调生成器；但把用户填的东西直接丢掉更糟。
先存到数据目录，配置完成后「角色 → 新建角色」会读它做预填，用过即删。
"""


def _stash_persona_brief(paths: Any, brief: Any) -> None:
    """把向导里填的角色描述写到数据目录，供「新建角色」预填。"""
    import json

    if not isinstance(brief, dict):
        return
    text = {k: v for k, v in brief.items() if isinstance(v, str) and v.strip()}
    if not text:
        return
    try:
        (paths.DATA_DIR / PERSONA_BRIEF_FILE).write_text(
            json.dumps(text, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("已暂存向导里的角色描述，配置完成后可在「角色 → 新建角色」里接着用")
    except OSError:
        logger.warning("暂存角色描述失败（不影响配置写入）", exc_info=True)


def _int_list(value: Any) -> list[int]:
    """把向导传来的名单统一成 int 列表。

    前端可能是 ``[123, 456]``、``"123,456"`` 或 ``"123\\n456"``（用户从别处粘贴），
    三种都要认；空值给空列表。
    """
    if value in (None, ""):
        return []
    if isinstance(value, str):
        parts = re.split(r"[,\s，、]+", value)
        return [int(p) for p in parts if p.strip()]
    return [int(x) for x in value]


def _build_features(payload: dict[str, Any], container_cls: Any, section_classes: dict[str, Any]):
    """按向导 payload 构造 FeaturesConfig。

    只处理 payload 里出现过的段——没出现的保持 schema 默认值，
    这样向导不必把每个字段都列出来。
    """
    kwargs: dict[str, Any] = {}
    for key, cls in section_classes.items():
        raw = payload.get(key)
        if isinstance(raw, dict):
            allowed = set(type(cls).__annotations__) | set(getattr(cls, "model_fields", {}))
            kwargs[key] = cls(**{k: v for k, v in raw.items() if k in allowed})
    return container_cls(**kwargs)


def _merge_features(existing: Any, incoming: Any) -> Any:
    """把向导给的功能段并进现有 features，只覆盖它显式提到的段。"""
    merged = existing.model_copy(deep=True)
    for key in type(incoming).model_fields:
        source = getattr(incoming, key)
        target = getattr(merged, key)
        # 只看"向导是否显式构造了这个段"：与默认值不同的段才算给了
        default = type(target)()
        if source.model_dump() != default.model_dump():
            setattr(merged, key, source)
    return merged


def _behavior_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    """向导里允许调的 behavior 字段（目前只有上下文上限）。"""
    out: dict[str, Any] = {}
    if "max_context_tokens" in payload:
        value = payload["max_context_tokens"]
        out["context"] = {"max_context_tokens": None if value in (None, "") else int(value)}
    return out


def _default_model_for(paths: Any, provider: Any, provider_id: str) -> str:
    """provider 没写 model 时，从预设里挑第一个。"""
    preset_id = getattr(provider, "preset", None) or provider_id
    try:
        from providers.presets_loader import load_presets_for

        presets = load_presets_for(paths)
    except Exception:  # noqa: BLE001
        return "deepseek-flash"
    preset = presets.get(preset_id)
    if preset is not None and getattr(preset, "models", None):
        return preset.models[0].id
    return "deepseek-flash"


__all__ = [
    "STATE_ERROR",
    "STATE_RUNNING",
    "STATE_SETUP_REQUIRED",
    "STATE_STARTING",
    "STATE_STOPPED",
    "RuntimeHost",
]
