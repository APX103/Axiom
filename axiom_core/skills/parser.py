"""SKILL.md 解析。



Skill = YAML frontmatter (name/description 必填) + markdown body (给模型的指令)。
简化: 去掉 when 守卫 / symlink 安全 / axiom_core- 前缀 / AST gate。
保留: frontmatter 解析 + 字节数上限 + 占位符替换 + 相对路径补全。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# SKILL.md 字节上限 (原版 gq, 约 65536)
SKILL_MAX_BYTES = 65536
# description 字符上限
DESCRIPTION_MAX = 1024
# name 校验 (对照原版 xnw: 小写字母数字和 -/)
NAME_RE = re.compile(r"^[a-z0-9\-/]+$")
# frontmatter 分隔
FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.S)


@dataclass
class Skill:
    """一个已解析的 skill。对照原版 _parseSkillFile 输出对象。"""

    name: str
    description: str
    body: str  # 给模型的指令 (去 frontmatter 后)
    base_dir: Path | None = None
    fold_cue: str | None = None
    source: str = "local"  # local / anthropic / draft
    # 索引用文本 (description + body 前 2000 字符)
    index_text: str = ""

    @property
    def content(self) -> str:
        """完整内容 (frontmatter + body 还原,供加载)。"""
        return self.body

    def metadata_tag(self) -> str:
        """生成 <skill-metadata> 标签。对照原版 0808.js:1234。"""
        return f'<skill-metadata name="{self.name}" source="{self.source}" />\n\n'


def parse_skill_md(content: str, base_dir: Path | None = None, source: str = "local") -> Skill:
    """解析 SKILL.md 内容。


    """
    if len(content.encode("utf-8")) > SKILL_MAX_BYTES:
        raise ValueError(f"SKILL.md exceeds {SKILL_MAX_BYTES} bytes — refusing to parse")

    # 去 frontmatter
    fm_match = FRONTMATTER_RE.match(content)
    frontmatter: dict[str, str] = {}
    body = content
    if fm_match:
        frontmatter = _parse_simple_yaml(fm_match.group(1))
        body = content[fm_match.end():]
    else:
        # 尝试无尾换行的 frontmatter
        body = content

    name = frontmatter.get("name", "").strip()
    description = frontmatter.get("description", "").strip()
    fold_cue = frontmatter.get("fold_cue")

    if not name:
        raise ValueError("SKILL.md missing required field: name")
    if not NAME_RE.match(name):
        raise ValueError(f"invalid skill name '{name}' (must match {NAME_RE.pattern})")
    if not description:
        raise ValueError("SKILL.md missing required field: description")
    if len(description) > DESCRIPTION_MAX:
        description = description[:DESCRIPTION_MAX]

    # 占位符替换 (对照原版 KLz)
    if base_dir is not None:
        body = body.replace("{baseDir}", str(base_dir))
        body = body.replace("{skillsRoot}", str(base_dir.parent))

    # 相对路径补全 (简化: 不做,原版 xLz/YLz 复杂)
    # 索引文本: description + body 前 2000 字符 (对照原版 TC_=2000)
    index_text = f"{description}\n{body[:2000]}"

    return Skill(
        name=name,
        description=description,
        body=body.strip(),
        base_dir=base_dir,
        fold_cue=fold_cue,
        source=source,
        index_text=index_text,
    )


def _parse_simple_yaml(yaml_str: str) -> dict[str, str]:
    """简易 YAML frontmatter 解析 (仅顶层 key: value)。

    原版用 yaml 库,这里简化为行级解析。
    只解析顶层 (无缩进) 的 key,跳过嵌套块 (metadata:/compute: 等的缩进行),
    避免嵌套块里的 key (如 metadata.third_party[].name) 覆盖顶层 name。
    """
    result: dict[str, str] = {}
    for line in yaml_str.splitlines():
        # 跳过缩进行 (嵌套块内容) 和空行/注释
        if line and line[0] in (" ", "\t"):
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # 跳过列表项和无值的块标记 (如 metadata:)
            if key and value:
                result[key] = value
    return result
