"""WebUI 主题常量 —— 从原 PySide6 的 ``ui/theme/qss.py`` 提取的纯数据部分。

只保留色板 / 间距 / 圆角 / 字号 / 字体族，去掉所有 Qt 依赖，
由 ``webui/static/app.css`` 通过 CSS 变量消费，保证 Web 端与桌面端同一套视觉。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Palette:
    """单个主题的完整色板。"""

    name: str             # "light" / "dark"

    bg_primary: str       # 背景主色
    bg_card: str          # 卡片 / 面板
    bg_hover: str         # 悬停浅填充
    border: str           # 分隔线 / 极轻边框

    text_primary: str     # 主要文字
    text_secondary: str   # 次要文字
    text_disabled: str    # 占位 / 禁用

    accent_primary: str   # 青瓷青
    accent_blue: str      # 汝窑蓝
    error: str            # 朱砂红
    warning: str          # 栀子黄
    success: str          # 苔色绿


LIGHT = Palette(
    name="light",
    bg_primary="#F8F5EE",
    bg_card="#FCFAF4",
    bg_hover="#F0E8D6",
    border="#E8E1CE",
    text_primary="#2B2622",
    text_secondary="#6B635A",
    text_disabled="#B5AC9F",
    accent_primary="#6FA39A",
    accent_blue="#5A7A99",
    error="#C0584F",
    warning="#D9A85F",
    success="#7A9B6E",
)


DARK = Palette(
    name="dark",
    bg_primary="#1A1817",
    bg_card="#232120",
    bg_hover="#2E2B28",
    border="#3A3633",
    text_primary="#E8E2D4",
    text_secondary="#9B9286",
    text_disabled="#5C544A",
    accent_primary="#6FA39A",
    accent_blue="#5A7A99",
    error="#C0584F",
    warning="#D9A85F",
    success="#7A9B6E",
)


# ============================================================
# 间距 / 圆角 / 字号
# ============================================================


class Spacing:
    XS = 4
    SM = 8
    MD = 16
    LG = 24
    XL = 40
    XXL = 64


class Radius:
    SMALL = 4
    DEFAULT = 8
    LARGE = 12
    PILL = 999


class FontSize:
    T1 = 28
    T2 = 20
    T3 = 16
    BODY = 14
    SECONDARY = 13
    SMALL = 12
    CODE = 13


# ============================================================
# 字体族（按优先级降序）
# ============================================================


SERIF_FAMILIES = (
    "Source Han Serif CN",
    "Noto Serif CJK SC",
    "STSong",
    "宋体",
    "serif",
)


SANS_FAMILIES = (
    "Source Han Sans CN",
    "Noto Sans CJK SC",
    "PingFang SC",
    "Microsoft YaHei UI",
    "微软雅黑",
    "Inter",
    "sans-serif",
)


MONO_FAMILIES = (
    "Sarasa Mono SC",
    "JetBrains Mono",
    "Consolas",
    "Courier New",
    "monospace",
)


def font_family(families: tuple[str, ...]) -> str:
    """构造 CSS 用的字体族字符串。"""
    return ", ".join(f'"{f}"' if " " in f else f for f in families)


__all__ = [
    "DARK",
    "LIGHT",
    "MONO_FAMILIES",
    "SANS_FAMILIES",
    "SERIF_FAMILIES",
    "FontSize",
    "Palette",
    "Radius",
    "Spacing",
    "font_family",
]
