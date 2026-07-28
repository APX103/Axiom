"""论文模板: 扫描内置 + 用户自定义模板目录。

模板 = 一个子目录, 含 template.tex (preamble 锁定) + meta.json (显示信息)。
解析目录的方式复用 skills/catalog.py: 源码 templates/ 或 PyInstaller _MEIPASS/templates。
用户自定义模板源: {data_dir}/templates/ (和 skills 的全局自定义目录同模式)。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TemplateMeta:
    """单个模板的元信息 (对应 meta.json)。"""

    id: str
    name: str
    description: str
    documentclass: str
    columns: int


def builtin_templates_root() -> Path | None:
    """内置模板根目录: 优先 PyInstaller bundle, 回退源码 templates/。"""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "templates")
    candidates.append(Path(__file__).resolve().parent.parent / "templates")
    return next((c for c in candidates if c.exists()), None)


def list_templates(data_dir: Path | None = None) -> list[TemplateMeta]:
    """列出所有可用模板: 内置 + 用户自定义 (data_dir/templates)。

    内置与自定义同名时, 自定义优先 (允许覆盖)。
    """
    by_id: dict[str, tuple[TemplateMeta, Path]] = {}

    for root in _scan_roots(data_dir):
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            meta_path = child / "meta.json"
            tex_path = child / "template.tex"
            if not (meta_path.exists() and tex_path.exists()):
                continue
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            meta = TemplateMeta(
                id=data.get("id", child.name),
                name=data.get("name", child.name),
                description=data.get("description", ""),
                documentclass=data.get("documentclass", "article"),
                columns=data.get("columns", 1),
            )
            by_id[meta.id] = (meta, tex_path)

    return [v[0] for v in by_id.values()]


def get_template_path(template_id: str, data_dir: Path | None = None) -> Path | None:
    """返回某模板的 template.tex 路径; 不存在返回 None。"""
    for root in _scan_roots(data_dir):
        tex = root / template_id / "template.tex"
        if tex.exists():
            return tex
    return None


def _scan_roots(data_dir: Path | None) -> list[Path]:
    """扫描根列表: 内置在前, 用户自定义在后 (后者覆盖前者)。"""
    roots: list[Path] = []
    builtin = builtin_templates_root()
    if builtin:
        roots.append(builtin)
    if data_dir is not None:
        user_root = data_dir / "templates"
        if user_root.exists():
            roots.append(user_root)
    return roots
