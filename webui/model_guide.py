"""本地模型插件的安装指引与目录识别（Qt-free）。

从原 ``ui/widgets/model_install_guide.py`` 提取：去掉 QProcess / QDialog /
QDesktopServices 相关实现，只保留纯粹的路径计算、依赖探测与文案生成。
WebUI 的「模型管理」页直接消费这些函数；真正的后台安装改用 asyncio 子进程，
见 ``webui/model_install.py``。
"""

from __future__ import annotations

import importlib.util
import shutil
from collections.abc import Callable
from pathlib import Path

from plugins import PluginRecord

_IMPORT_NAME_OVERRIDES = {
    "sentence-transformers": "sentence_transformers",
}
PIP_SOURCES: dict[str, tuple[str, str]] = {
    "tsinghua": ("清华源", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    "aliyun": ("阿里源", "https://mirrors.aliyun.com/pypi/simple"),
    "official": ("官方源", ""),
}


def required_model_paths(record: PluginRecord) -> list[Path]:
    """返回该模型安装完成后应存在的文件路径。"""
    base = record.meta.resolve_model_dir()
    sources = [s for s in record.meta.download_sources if s.required]
    if not sources:
        return [base]
    return [base / s.dest_filename for s in sources]


def build_model_install_markdown(
    record: PluginRecord,
    *,
    project_root: Path | None = None,
) -> str:
    """生成可给用户看的图文安装指引 Markdown。"""
    meta = record.meta
    target_dir = meta.resolve_model_dir()
    required = required_model_paths(record)
    files = "\n".join(f"- `{p}`" for p in required)
    repo_url = meta.download_url or "该模型未提供下载页面"
    extra = _extra_install_markdown(record, project_root=project_root)
    return f"""# {meta.display_name} 安装指引

## 先做这两步

1. 打开模型页面：`{repo_url}`
2. 下载完整仓库后，把文件夹拖进模型管理页。

目标目录：`{target_dir}`

## 需要识别到的文件

{files}

## 操作

拖入后 Debata 会复制到正确目录。复制完成后点「重新扫描」，状态变成「已下载」即可。
{extra}
"""


def missing_python_deps(record: PluginRecord) -> list[str]:
    """返回当前 Python 环境缺失的插件运行依赖。"""
    missing: list[str] = []
    for dep in record.meta.python_deps:
        import_name = _requirement_to_import_name(dep)
        if import_name and importlib.util.find_spec(import_name) is None:
            missing.append(dep)
    return missing


def pip_install_args(deps: list[str], source_key: str = "tsinghua") -> list[str]:
    """构造 ``python <这些参数>`` 形式的 pip 安装参数。"""
    args = ["-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
    _label, index_url = PIP_SOURCES.get(source_key, PIP_SOURCES["tsinghua"])
    if index_url:
        args.extend(["-i", index_url])
    args.extend(deps)
    return args


def dependency_install_env(base_env: dict[str, str]) -> dict[str, str]:
    """清掉代理变量再装依赖，避免继承来的代理把 pip 打死。"""
    env = dict(base_env)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        env.pop(name, None)
    return env


def find_matching_record_for_folder(
    folder: Path,
    records: list[PluginRecord],
) -> tuple[PluginRecord, Path] | None:
    """根据拖入文件夹内容匹配模型。

    返回 (record, source_root)。source_root 是应复制到 meta.resolve_model_dir() 的目录。
    """
    if not folder.is_dir():
        return None
    candidates: list[tuple[int, int, PluginRecord, Path]] = []
    for record in records:
        required = [s.dest_filename for s in record.meta.download_sources if s.required]
        if not required:
            continue
        direct = _match_score(folder, required)
        if direct == len(required):
            candidates.append((direct, len(folder.parts), record, folder))
        best_nested: tuple[int, Path] = (0, folder)
        for child in folder.rglob("*"):
            if child.is_dir():
                score = _match_score(child, required)
                if score > best_nested[0]:
                    best_nested = (score, child)
        if best_nested[0] == len(required):
            candidates.append(
                (best_nested[0], len(best_nested[1].parts), record, best_nested[1])
            )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2], candidates[0][3]


def install_model_folder(
    source_root: Path,
    record: PluginRecord,
    progress: Callable[[int, int, Path], None] | None = None,
) -> None:
    """把识别出的模型目录复制到插件目标目录。

    支持两种常见拖入方式：
    - 拖入仓库根目录：目录中已包含 download_sources 的相对路径。
    - 拖入模型子目录：目录中直接包含 model.bin/config.json 等文件。
    """
    target = record.meta.resolve_model_dir()
    target.mkdir(parents=True, exist_ok=True)
    target_prefix = _infer_target_prefix(source_root, record)
    files = [src for src in source_root.rglob("*") if src.is_file()]
    total = len(files)
    for idx, src in enumerate(files, start=1):
        rel = src.relative_to(source_root)
        dst = target / target_prefix / rel
        try:
            if src.resolve() == dst.resolve():
                if progress is not None:
                    progress(idx, total, src)
                continue
        except OSError:
            pass
        dst.parent.mkdir(parents=True, exist_ok=True)
        if progress is not None:
            progress(idx - 1, total, src)
        shutil.copy2(src, dst)
        if progress is not None:
            progress(idx, total, src)


def _infer_target_prefix(source_root: Path, record: PluginRecord) -> Path:
    required = [Path(s.dest_filename) for s in record.meta.download_sources if s.required]
    if not required:
        return Path()
    if all((source_root / rel).is_file() for rel in required):
        return Path()
    if not all((source_root / rel.name).is_file() for rel in required):
        return Path()
    parents = [rel.parent for rel in required]
    first = parents[0]
    if first == Path("."):
        return Path()
    if all(parent == first for parent in parents):
        return first
    return Path()


def _match_score(root: Path, required: list[str]) -> int:
    score = 0
    for rel in required:
        rel_path = Path(rel)
        if (root / rel_path).is_file():
            score += 1
        elif (root / rel_path.name).is_file():
            score += 1
    return score


def _requirement_to_import_name(requirement: str) -> str:
    name = requirement.split(";", 1)[0].strip()
    if "[" in name:
        name = name.split("[", 1)[0].strip()
    for marker in (">=", "==", "~=", "<=", "!=", ">", "<"):
        if marker in name:
            name = name.split(marker, 1)[0].strip()
            break
    return _IMPORT_NAME_OVERRIDES.get(name, name.replace("-", "_"))


def _extra_install_markdown(
    record: PluginRecord,
    *,
    project_root: Path | None = None,
) -> str:
    if record.meta.name != "voxcpm2":
        return ""
    root = Path(project_root) if project_root is not None else Path.cwd()
    ffmpeg_dir = root / "data" / "tools" / "ffmpeg" / "bin"
    return f"""

## VoxCPM2 降噪需要 FFmpeg

开启降噪时必须准备 Windows **full-shared** 版 FFmpeg。静态版只有 exe，不够用。

下载：

- [FFmpeg 官方下载页](https://ffmpeg.org/download.html)
- [gyan.dev Windows builds](https://www.gyan.dev/ffmpeg/builds/)：选 `full-shared`，解压后复制 `bin`

放到这里：

`{ffmpeg_dir}`

目录里应包含：

- `ffmpeg.exe`
- `avutil-*.dll`
- `avcodec-*.dll`
- `avformat-*.dll`
"""


__all__ = [
    "PIP_SOURCES",
    "build_model_install_markdown",
    "dependency_install_env",
    "find_matching_record_for_folder",
    "install_model_folder",
    "missing_python_deps",
    "pip_install_args",
    "required_model_paths",
]
