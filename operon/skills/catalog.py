"""Skill catalog: 从磁盘扫描 + 注册表。

Skill 是工具级配置: 全局安装,所有会话共享。
扫描来源:
- 内置 skills: 随安装包/源码携带的 skills/skills/。
- 全局自定义 skills: {data_dir}/skills/。
禁用列表 (disabled_skills) 是 session 级启用配置,在创建会话时应用。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .parser import Skill, parse_skill_md

logger = logging.getLogger(__name__)


class SkillCatalog:
    """Skill 目录。对照原版 SkillCatalog (0799.js)。

    支持从多个目录扫描 skill,并按 session 级 disabled 列表过滤。
    """

    def __init__(self, skills_root: Path | None = None):
        self.skills_root = skills_root
        self._skills: dict[str, Skill] = {}  # name → Skill
        self._disabled: set[str] = set()
        if skills_root and skills_root.exists():
            self.scan_dir(skills_root, source="local")

    def scan_dir(self, path: Path, *, source: str = "local") -> int:
        """扫描单个目录,返回加载的 skill 数。对照原版 _scanDisk (0799.js:280)。"""
        if not path or not path.exists():
            return 0
        count = 0
        for child in sorted(path.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                content = skill_md.read_text(encoding="utf-8")
                skill = parse_skill_md(content, base_dir=child, source=source)
                if skill.name not in self._disabled:
                    self._skills[skill.name] = skill
                    count += 1
            except Exception as e:
                logger.warning("failed to parse skill %s: %s", child.name, e)
        return count

    def add(self, skill: Skill) -> None:
        """手动注册一个 skill。"""
        if skill.name not in self._disabled:
            self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        """精确获取。对照原版 resolve (0799.js:117)。"""
        return self._skills.get(name)

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def set_disabled(self, names: list[str]) -> None:
        """全局禁用。对照原版 setGloballyDisabled (0796.js:255)。"""
        self._disabled = set(names)
        for n in names:
            self._skills.pop(n, None)

    def fuzzy_suggest(self, name: str, *, max_results: int = 3) -> list[str]:
        """模糊建议 (Levenshtein)。对照原版 hLz (0806.js:25)。"""
        import difflib

        names = list(self._skills.keys())
        if not names:
            return []
        matches = difflib.get_close_matches(name, names, n=max_results, cutoff=0.6)
        return matches


def _load_skills_from_dir(root: Path, source: str) -> list[Skill]:
    """通用: 扫描一个目录下的所有 skill。"""
    skills: list[Skill] = []
    if not root.exists():
        return skills
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
            s = parse_skill_md(content, base_dir=child, source=source)
            skills.append(s)
        except Exception as e:
            logger.warning("failed to load skill %s: %s", child.name, e)
    return skills


def load_builtin_skills() -> list[Skill]:
    """加载内置 skill — 从 operon-py/skills/ 目录的真实 SKILL.md。

    对照原版: 原版内置 skill 在 44MB assets.tar 里 (./skills/<name>/SKILL.md)。
    本项目从原版提取了通用 skill 到 operon-py/skills/。
    每个含真实指令 + 可选 kernel.py sidecar。

    路径解析 (两种部署形态都要支持):
    1. PyInstaller 打包后: skills/ 在 spec 里被加进 datas, 解压到 _MEIPASS/skills/。
       用 sys._MEIPASS 定位 (frozen 时 sys.frozen=True)。
    2. 源码运行: 相对 catalog.py 的 ../../skills/skills/。
    """
    builtin_root = _builtin_root()
    if builtin_root is None:
        logger.warning("builtin skills directory not found")
        return []
    return _load_skills_from_dir(builtin_root, source="anthropic")


def load_global_skills(data_dir: Path) -> list[Skill]:
    """加载全局自定义 skill — 从 {data_dir}/skills/。

    Skill 是工具级配置,应放在一处由所有会话共享,而不是复制到每个工作区。
    """
    return _load_skills_from_dir(data_dir / "skills", source="global")


def load_claude_skills() -> list[Skill]:
    """加载 Claude Code 用户级 skill 目录 — ~/.claude/skills/。"""
    return _load_skills_from_dir(Path.home() / ".claude" / "skills", source="claude")


def load_project_skills(workspace: Path) -> list[Skill]:
    """加载项目级 skill 目录 — {workspace}/.axiom/skills/。

    只扫描用户主动放置的目录,Axiom 不会自动创建或复制 skill 到工作区。
    """
    return _load_skills_from_dir(workspace / ".axiom" / "skills", source="project")


def load_custom_skills(paths: list[str]) -> list[Skill]:
    """加载用户配置的额外 skill 目录列表。"""
    skills: list[Skill] = []
    for p in paths:
        if not p:
            continue
        skills.extend(_load_skills_from_dir(Path(p).expanduser().resolve(), source="custom"))
    return skills


def _builtin_root() -> Path | None:
    """返回 builtin skills 根目录 (PyInstaller _MEIPASS 优先, 回退源码路径)。"""
    import sys

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "skills" / "skills")
    candidates.append(Path(__file__).resolve().parent.parent.parent / "skills" / "skills")
    return next((c for c in candidates if c.exists()), None)


def ensure_user_skills_copy(data_dir: Path) -> Path | None:
    """首次启动时把 builtin skills copy 一份到 <data_dir>/skills/。

    目的: 让用户在设置面板里看到、可编辑内置 skills (否则打包后用户不可见)。
    只在目标目录不存在时 copy (不覆盖用户已修改的副本)。
    返回 copy 到的路径 (或已存在的路径)。
    """
    src = _builtin_root()
    if src is None:
        return None
    dst = data_dir / "skills"
    if dst.exists():
        return dst  # 已 copy 过 (用户可能改过), 不覆盖
    try:
        import shutil

        shutil.copytree(src, dst)
        logger.info("copied builtin skills to %s", dst)
        return dst
    except Exception as e:
        logger.warning("failed to copy builtin skills to %s: %s", dst, e)
        return None
