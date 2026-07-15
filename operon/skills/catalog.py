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
    本项目从原版提取了 13 个通用 skill (排除 16 个生物领域 skill) 到 operon-py/skills/。
    每个含真实指令 + 可选 kernel.py sidecar。
    """
    from pathlib import Path

    from .parser import parse_skill_md

    # 内置 skill 目录 (打包在 operon-py/skills/skills/)
    builtin_root = Path(__file__).resolve().parent.parent.parent / "skills" / "skills"
    skills: list[Skill] = []
    if not builtin_root.exists():
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
