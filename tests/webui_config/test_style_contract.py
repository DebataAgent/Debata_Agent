"""WebUI 样式契约测试。

目的不是复刻 CSS 实现，而是锁住 ``docs/ui_style_guide.md`` 里那些
"改了就会破坏两端一致性"的硬约束：

    §二 色板    —— 必须是 webui/theme.py 里的那一套，不许在 CSS 里手写别的色值
    §三 字体    —— 衬线标题 + 字号阶梯
    §五 组件    —— 按钮四态、输入框焦点、徽章实心
    §六 图标    —— 不用 emoji 当图标
    §七 动效    —— 时长变量存在

刻意保持少量断言：断言过多会变成脆弱的实现测试，改样式就得改测试。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
APP_CSS = PROJECT_ROOT / "webui" / "static" / "app.css"
APP_JS = PROJECT_ROOT / "webui" / "static" / "js" / "app.js"

pytestmark = pytest.mark.skipif(
    not APP_CSS.exists(), reason="WebUI 静态资源不存在"
)


def _css() -> str:
    return APP_CSS.read_text(encoding="utf-8")


def _js() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ============================================================
# §二 色板：theme.py 是唯一真源
# ============================================================


def _theme_hex_values() -> set[str]:
    from dataclasses import fields

    from webui.theme import DARK, LIGHT

    values: set[str] = set()
    for palette in (LIGHT, DARK):
        for field in fields(palette):
            value = getattr(palette, field.name)
            if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                values.add(value.upper())
    return values


def test_every_theme_color_appears_in_css():
    """theme.py 的每个色值都应出现在 app.css，防止两端色值漂移。"""
    css = _css().upper()
    missing = sorted(v for v in _theme_hex_values() if v not in css)
    assert not missing, f"这些 theme.py 色值没出现在 app.css: {missing}"


def test_css_does_not_invent_palette_colors():
    """CSS 里不该出现色板之外的自造十六进制色（#fff / #000 这类基础色除外）。

    允许列表很小：纯白与纯黑是按钮文字/遮罩的通用做法。
    """
    allowed = _theme_hex_values() | {"#FFF", "#FFFFFF", "#000", "#000000"}
    found = {m.upper() for m in re.findall(r"#[0-9A-Fa-f]{6}\b", _css())}
    invented = sorted(found - allowed)
    assert not invented, f"app.css 里出现了色板之外的自造色: {invented}"


# ============================================================
# §三 字体与字号阶梯
# ============================================================


@pytest.mark.parametrize(
    "token",
    ["--font-serif", "--font-sans", "--font-mono"],
)
def test_font_tokens_defined(token):
    assert token in _css(), f"缺少字体 token {token}"


@pytest.mark.parametrize(
    "token,size",
    [
        ("--fs-t1", "28px"),
        ("--fs-t2", "20px"),
        ("--fs-t3", "16px"),
        ("--fs-body", "14px"),
        ("--fs-secondary", "13px"),
        ("--fs-small", "12px"),
        ("--fs-code", "13px"),
    ],
)
def test_font_size_ladder_matches_guide(token, size):
    """字号阶梯必须与 docs/ui_style_guide.md §三 和 theme.py 的 FontSize 一致。"""
    assert re.search(rf"{re.escape(token)}\s*:\s*{re.escape(size)}", _css()), (
        f"{token} 应为 {size}"
    )


def test_body_sets_cjk_letter_spacing():
    """指南 §三：所有中文字体设 letter-spacing: 0.02em。"""
    assert re.search(r"letter-spacing:\s*\.02em", _css())


def test_page_title_uses_serif():
    """指南 §三：T2 小节标题用思源宋体。"""
    block = re.search(r"\.page-title\s*\{[^}]*\}", _css())
    assert block, ".page-title 规则不存在"
    assert "var(--font-serif)" in block.group(0)


# ============================================================
# §四 圆角
# ============================================================


def test_card_radius_is_8px_not_12px():
    """指南 §四：卡片 8px，12px 只留给弹窗。"""
    block = re.search(r"\.card\s*\{[^}]*\}", _css())
    assert block, ".card 规则不存在"
    assert "var(--radius-card)" in block.group(0)


def test_modal_radius_is_12px():
    block = re.search(r"\.modal\s*\{[^}]*\}", _css())
    assert block, ".modal 规则不存在"
    assert "var(--radius-lg)" in block.group(0)


# ============================================================
# §五 组件四态
# ============================================================


@pytest.mark.parametrize(
    "selector",
    [
        r"\.btn:hover",
        r"\.btn:active",
        r"\.btn:disabled",
        r"\.btn-primary:hover",
        r"\.btn-primary:active",
        r"\.btn-danger:hover",
        r"\.btn-text",
    ],
)
def test_button_states_present(selector):
    """指南 §五：主按钮 hover 加深 8% / pressed 加深 16%，次按钮与文字按钮各有其态。"""
    assert re.search(selector + r"\s*[,{]", _css()), f"缺少按钮规则 {selector}"


def test_focus_visible_outline_present():
    """指南 §十：焦点态用青瓷青 2px 外环。"""
    block = re.search(r":focus-visible\s*\{[^}]*\}", _css())
    assert block, "缺少 :focus-visible 规则"
    assert "outline" in block.group(0)


def test_input_focus_has_glow_and_error_state():
    """指南 §五：输入框焦点加外发光，错误态边框朱砂红。"""
    css = _css()
    assert "--focus-ring" in css
    assert re.search(r"input\.has-error", css), "缺少输入框错误态"


def test_badge_states_are_solid():
    """指南 §五：状态徽章用实心色底白字（idle 例外，用描边）。"""
    css = _css()
    for state in ("ok", "warn", "bad"):
        block = re.search(rf"\.badge\.{state}\s*\{{[^}}]*\}}", css)
        assert block, f"缺少 .badge.{state}"
        assert "color: #fff" in block.group(0), f".badge.{state} 应为实心白字"
    idle = re.search(r"\.badge\.idle\s*\{[^}]*\}", css)
    assert idle and "background: transparent" in idle.group(0)


def test_selected_states_use_left_bar():
    """指南 §五：列表/导航选中态 = 左侧 3px 竖条 + 浅填充。"""
    css = _css()
    assert "--accent-soft" in css
    bar = re.search(r"\.nav-item::before\s*\{[^}]*\}", css)
    assert bar and "3px" in bar.group(0), "导航选中态缺左竖条"
    active = re.search(r"\.nav-item\.active\s*\{[^}]*\}", css)
    assert active and "--accent-soft" in active.group(0), "导航选中态缺浅填充"


# ============================================================
# §七 动效
# ============================================================


@pytest.mark.parametrize(
    "token",
    ["--transition-fast", "--transition-list", "--transition-page", "--transition-modal"],
)
def test_transition_tokens_defined(token):
    assert token in _css(), f"缺少动效 token {token}"


def test_theme_switch_transitions():
    """指南 §九：主题切换用 200ms 过渡，避免硬切感。"""
    block = re.search(r"^html\s*\{[^}]*\}", _css(), re.MULTILINE)
    assert block, "缺少 html 规则"
    assert "var(--transition-modal)" in block.group(0)


# ============================================================
# §六 图标：导航不能用 emoji
# ============================================================


# emoji / 符号图标的 Unicode 区段。刻意不写成 "ord(c) > 0x2100"——
# 那样会把中文标题（CJK 在 U+4E00 以上）一起误判。
_ICON_GLYPH_RANGES = (
    (0x2190, 0x21FF),   # 箭头
    (0x2300, 0x23FF),   # 技术符号（⏻ 等）
    (0x25A0, 0x25FF),   # 几何图形（◈ ▤ 等）
    (0x2600, 0x27BF),   # 杂项符号 / 装饰符号（☾ ⚙ ⛔ ⟳）
    (0x2B00, 0x2BFF),   # 杂项符号与箭头
    (0x1F000, 0x1FAFF),  # emoji 主体
    (0xFE0F, 0xFE0F),   # 变体选择符
)


def _icon_glyphs(text: str) -> list[str]:
    return sorted({
        c for c in text
        if any(lo <= ord(c) <= hi for lo, hi in _ICON_GLYPH_RANGES)
    })


def test_nav_items_avoid_emoji_icons():
    """指南 §六：用线性图标，禁止面性/emoji 字符当图标。"""
    block = re.search(r"const NAV_ITEMS = \[(.*?)\];", _js(), re.DOTALL)
    assert block, "找不到 NAV_ITEMS"
    offenders = _icon_glyphs(block.group(1))
    assert not offenders, f"NAV_ITEMS 里还有 emoji 图标: {offenders}"


def test_nav_icons_all_exist_in_icons_module():
    """NAV_ITEMS 的 icon 名必须在 icons.js 的 PATHS 里，否则导航会画空白。"""
    icons_js = (PROJECT_ROOT / "webui" / "static" / "js" / "icons.js").read_text(
        encoding="utf-8"
    )
    paths_block = re.search(r"const PATHS = \{(.*?)\n\};", icons_js, re.DOTALL)
    assert paths_block, "找不到 PATHS"
    defined = set(re.findall(r"^\s{2}([a-z_]+):\s*\[", paths_block.group(1), re.MULTILINE))
    assert defined, "PATHS 解析为空"

    block = re.search(r"const NAV_ITEMS = \[(.*?)\];", _js(), re.DOTALL)
    used = set(re.findall(r"icon:\s*'([^']+)'", block.group(1)))
    assert used, "NAV_ITEMS 没有 icon 字段"
    missing = sorted(used - defined)
    assert not missing, f"NAV_ITEMS 引用了 icons.js 里不存在的图标: {missing}"


def test_topbar_buttons_have_no_emoji_glyphs():
    """顶栏按钮的图标也走 icons.js，HTML 里不该留 emoji 字符。"""
    html = (PROJECT_ROOT / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    offenders = _icon_glyphs(html)
    assert not offenders, f"index.html 里还有 emoji 图标字符: {offenders}"


# ============================================================
# ES 模块图完整性
# ============================================================


JS_DIR = PROJECT_ROOT / "webui" / "static" / "js"
_IMPORT_RE = re.compile(
    r"""\bfrom\s+['"](\.[^'"]+)['"]|\bimport\s+['"](\.[^'"]+)['"]"""
)


def _js_modules() -> list:
    return sorted(JS_DIR.rglob("*.js"))


def test_js_modules_exist():
    assert _js_modules(), "找不到任何前端模块"


def test_relative_imports_resolve():
    """每个相对 import 都必须指向真实存在的文件。

    这条不是吹毛求疵：前端是原生 ES 模块、没有构建步骤，模块图是浏览器在
    **执行任何代码之前**整体解析的——只要有一个 import 404，入口模块就不会执行，
    页面直接全白、控制台之外毫无提示。

    实际踩过：persona_dialogs.js 在 static/js/ 下却写了 '../api.js'，
    指向不存在的 static/api.js，整个 WebUI 白屏。
    也别指望 node --check：它只查语法，不解析模块路径。
    """
    broken = []
    for path in _js_modules():
        text = path.read_text(encoding="utf-8")
        for match in _IMPORT_RE.finditer(text):
            spec = match.group(1) or match.group(2)
            if not (path.parent / spec).resolve().exists():
                broken.append(f"{path.relative_to(JS_DIR)} → {spec}")
    assert not broken, "这些 import 指向不存在的文件（会导致整页白屏）：\n" + "\n".join(broken)


def test_entry_module_is_reachable_from_index():
    """index.html 必须引到 app.js，且 app.js 能顺着 import 走到每个页面模块。"""
    html = (PROJECT_ROOT / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "/static/js/app.js" in html, "index.html 没引入入口模块"

    import re as _re

    app_js = (JS_DIR / "app.js").read_text(encoding="utf-8")
    # app.js 动态 import 了 pages/*.js，逐个确认存在
    referenced = _re.findall(r"""from\s+['"]\./(pages/[^'"]+|setup/[^'"]+)['"]""", app_js)
    assert referenced, "app.js 没有引入任何页面模块"
    for spec in referenced:
        assert (JS_DIR / spec).is_file(), f"app.js 引用了不存在的页面模块 {spec}"


# ============================================================
# 启动流程：不许"先 await 后端再决定显示什么"
# ============================================================


def test_boot_renders_before_awaiting_backend():
    """启动时必须先把界面亮出来，再发请求。

    三个顶层容器（#login-gate / #setup-gate / #shell）在 index.html 里都是
    ``hidden``。如果 boot() 先 ``await`` 后端再决定显示哪一个，那么后端一旦
    不回应（探测密钥、换模型、本机代理挂死），哪个容器都不会显示 ——
    页面纯白，且控制台没有任何报错，用户只能看到一片空白。

    锁住这个顺序：showLoading() 必须出现在第一个 await 之前。
    """
    js = _js()
    lines = js.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if re.match(r"async function boot\(\)\s*\{", line)),
        None,
    )
    assert start is not None, "找不到 boot()"
    # 顶层函数的结束就是下一行以 '}' 开头的行
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("}")),
        len(lines),
    )
    body = "\n".join(lines[start:end])

    # 注释里会出现 "await" 这个词（上面就在解释这件事），比较前先去掉注释行
    code = "\n".join(
        line for line in body.splitlines()
        if not line.strip().startswith(("//", "*", "/*"))
    )

    call = code.find("showLoading()")
    first_await = code.find("await ")
    assert call != -1, "boot() 没有调用 showLoading()，启动阶段会留白"
    assert first_await != -1, "boot() 里没有 await？结构变了，请更新这条测试"
    assert call < first_await, (
        "showLoading() 必须在 boot() 的第一个 await 之前调用，"
        "否则后端卡住时页面全白"
    )


def test_boot_failure_is_visible():
    """启动失败必须落在页面里看得见，不能只往控制台打日志。"""
    js = _js()
    assert "showBootError" in js, "缺少启动失败的可见兜底"
    assert re.search(r"boot\(\)\.catch", js), "boot() 的 reject 没有被接住"


def test_requests_have_timeout():
    """所有请求都要有超时，否则后端卡住会把 await 永远吊住。"""
    api_js = (JS_DIR / "api.js").read_text(encoding="utf-8")
    assert "AbortController" in api_js, "api.js 没有给请求加超时控制"
    assert re.search(r"REQUEST_TIMEOUT_MS\s*=\s*\d+", api_js), "缺少默认超时值"
