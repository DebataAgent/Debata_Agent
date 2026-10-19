"""加载预设目录下的所有 provider 预设。

内置预设来自仓库的 ``providers/presets/``；用户自定义预设放在数据目录的
``providers/presets/``（``AppPaths.USER_PROVIDER_PRESETS_DIR``），同名 id 覆盖内置。
普通调用点用 :func:`load_presets_for` 即可拿到合并结果。

预设目录结构：
    providers/presets/{name}/
        preset.yaml        # 预设元信息
        tutorial/          # 图文教程（用户后续填充）
            *.md
            *.png
        icon.png           # （可选）图标

preset.yaml 格式：
    id: deepseek               # 唯一标识（必填，小写下划线）
    display_name: DeepSeek
    protocol: openai_compat    # openai_compat / anthropic / ...
    base_url: https://api.deepseek.com
    registration_url: https://platform.deepseek.com
    reasoning_style: thinking_extra_body   # OpenAI 兼容协议时的思考参数风格
    models:
      - id: deepseek-chat
        display_name: DeepSeek V4
        capabilities: [chat, tool_call, reasoning]
        context_length: 128000
      - id: deepseek-reasoner
        ...
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)
_PRESETS_CACHE: dict[tuple[Path, ...], dict[str, ProviderPreset]] = {}


@dataclass(slots=True)
class ModelInfo:
    id: str
    display_name: str = ""
    capabilities: list[str] = field(default_factory=list)
    context_length: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> ModelInfo:
        return cls(
            id=str(data.get("id", "")),
            display_name=str(data.get("display_name", data.get("id", ""))),
            capabilities=list(data.get("capabilities", []) or []),
            context_length=int(data.get("context_length", 0) or 0),
        )


@dataclass(slots=True)
class ProviderPreset:
    """一个预设的所有元信息。"""

    id: str
    """唯一标识（小写、下划线）"""

    display_name: str
    protocol: str
    base_url: str

    models: list[ModelInfo] = field(default_factory=list)
    registration_url: str = ""
    reasoning_style: str = "thinking_extra_body"
    """仅 openai_compat 协议生效"""

    tutorial_dir: Path | None = None
    icon: Path | None = None

    @classmethod
    def from_yaml(cls, yaml_path: Path) -> ProviderPreset:
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if not data.get("id"):
            raise ValueError(f"预设缺 id 字段: {yaml_path}")

        preset_dir = yaml_path.parent
        tutorial = preset_dir / "tutorial"
        icon = preset_dir / "icon.png"

        return cls(
            id=str(data["id"]).lower(),
            display_name=str(data.get("display_name", data["id"])),
            protocol=str(data.get("protocol", "openai_compat")),
            base_url=str(data.get("base_url", "")),
            models=[ModelInfo.from_dict(m) for m in data.get("models", [])],
            registration_url=str(data.get("registration_url", "")),
            reasoning_style=str(data.get("reasoning_style", "thinking_extra_body")),
            tutorial_dir=tutorial if tutorial.is_dir() else None,
            icon=icon if icon.exists() else None,
        )

    def model_ids(self) -> list[str]:
        return [m.id for m in self.models]


def _load_one_dir(presets_dir: Path, result: dict[str, ProviderPreset]) -> None:
    """把单个目录下的预设并入 result（同名 id 覆盖，调用方保证顺序）。"""
    for d in sorted(presets_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or d.name.startswith("."):
            continue
        yaml_path = d / "preset.yaml"
        if not yaml_path.exists():
            logger.debug(f"跳过无 preset.yaml 的目录: {d}")
            continue
        try:
            preset = ProviderPreset.from_yaml(yaml_path)
        except Exception as e:
            logger.error(f"加载预设失败 {yaml_path}: {e}")
            continue
        if preset.id in result:
            logger.info(f"预设 {preset.id} 被覆盖: {yaml_path}")
        result[preset.id] = preset


def load_all_presets(
    presets_dir: Path,
    *,
    extra_dirs: Sequence[Path] = (),
    force_reload: bool = False,
) -> dict[str, ProviderPreset]:
    """扫描一个或多个预设目录，加载每个子目录的 preset.yaml。

    参数：
        presets_dir: 主目录（内置预设）。
        extra_dirs:  追加目录，**按顺序覆盖**前面的同名 id。用于用户数据目录里的
                     自定义预设——这样用户不必去改仓库内的 providers/presets/。

    返回 {id: ProviderPreset}。缺失的 extra 目录只记 debug 日志，不报错
    （首次启动时用户目录还没建是正常情况）。
    """
    dirs = [presets_dir.resolve()]
    dirs += [Path(d).resolve() for d in extra_dirs]

    cache_key = tuple(dirs)
    if not force_reload and cache_key in _PRESETS_CACHE:
        return dict(_PRESETS_CACHE[cache_key])

    result: dict[str, ProviderPreset] = {}
    for d in dirs:
        if not d.exists():
            logger.debug(f"预设目录不存在，跳过: {d}")
            continue
        _load_one_dir(d, result)

    _PRESETS_CACHE[cache_key] = dict(result)
    logger.info(f"加载 {len(result)} 个预设: {sorted(result.keys())}")
    return dict(result)


def load_presets_for(paths: Any) -> dict[str, ProviderPreset]:
    """按 AppPaths 加载内置 + 用户预设。

    调用点统一走这里，避免每处都手写两个目录名。
    """
    return load_all_presets(
        paths.PROVIDER_PRESETS_DIR,
        extra_dirs=[paths.USER_PROVIDER_PRESETS_DIR],
    )
