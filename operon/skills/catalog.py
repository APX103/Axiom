"""Skill catalog: 从磁盘扫描 + 注册表。



简化: 只 disk 来源 (扫 <workspace>/.claude/skills/<name>/SKILL.md)。
保留: 扫描 + 缓存 + 视图过滤 (disabled)。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .parser import Skill, parse_skill_md

logger = logging.getLogger(__name__)


class SkillCatalog:
    """Skill 目录。对照原版 SkillCatalog (0799.js)。

    扫描工作区的 .claude/skills/ 目录,解析每个 SKILL.md。
    """

    def __init__(self, skills_root: Path | None = None):
        self.skills_root = skills_root
        self._skills: dict[str, Skill] = {}  # name → Skill
        self._disabled: set[str] = set()
        if skills_root and skills_root.exists():
            self.scan()

    def scan(self) -> int:
        """扫描 skills_root,返回加载的 skill 数。对照原版 _scanDisk (0799.js:280)。"""
        if not self.skills_root or not self.skills_root.exists():
            return 0
        count = 0
        for child in sorted(self.skills_root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                content = skill_md.read_text(encoding="utf-8")
                skill = parse_skill_md(content, base_dir=child, source="local")
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
    from pathlib import Path
    import sys

    from .parser import parse_skill_md

    # 候选 roots: PyInstaller 解压目录优先, 回退源码相对路径
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "skills" / "skills")
    candidates.append(Path(__file__).resolve().parent.parent.parent / "skills" / "skills")

    builtin_root: Path | None = next((c for c in candidates if c.exists()), None)
    skills: list[Skill] = []
    if builtin_root is None:
        logger.warning("builtin skills directory not found in: %s", candidates)
        return skills
    for child in sorted(builtin_root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
            s = parse_skill_md(content, base_dir=child, source="anthropic")
            skills.append(s)
        except Exception as e:
            logger.warning("failed to load builtin skill %s: %s", child.name, e)
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
