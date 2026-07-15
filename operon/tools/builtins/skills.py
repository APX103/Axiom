"""Skill 工具: search_skills / skill。

对应原版: 0808.js:206 search_skills + 0808.js:1060 skill + 0808.js:1145 _executeLocalTool。
"""

from __future__ import annotations

from operon.tools.context import ToolContext


async def search_skills(ctx: ToolContext, query: str, max_results: int = 4) -> str:
    """词法检索 skill 目录。对照原版 search_skills (0808.js:206)。"""
    catalog = getattr(ctx, "skill_catalog", None)
    if catalog is None:
        return "(skill catalog not configured)"
    skills = catalog.list()
    if not skills:
        return "(no skills available)"
    from operon.skills.search import search_skills as _search

    results = _search(skills, query, max_results=max_results)
    if not results:
        suggestions = catalog.fuzzy_suggest(query)
        if suggestions:
            return f"No skills match '{query}'. Did you mean: {', '.join(suggestions)}?"
        return f"No skills match '{query}'."
    lines = [f"Skills matching '{query}' ({len(results)}):", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.skill.name} (score={r.score:.4f})")
        lines.append(f"   {r.skill.description[:150]}")
    return "\n".join(lines)


async def skill(ctx: ToolContext, skill: str) -> str:
    """加载并激活一个 skill。对照原版 skill 工具 (0808.js:1060)。

    返回 skill 正文 + metadata 标签 (注入到 agent 上下文)。
    """
    catalog = getattr(ctx, "skill_catalog", None)
    if catalog is None:
        return "(skill catalog not configured)"
    s = catalog.get(skill)
    if s is None:
        suggestions = catalog.fuzzy_suggest(skill)
        if suggestions:
            return f"Skill '{skill}' not found. Did you mean: {', '.join(suggestions)}?"
        return f"Skill '{skill}' not found. Use search_skills to find available skills."
    # 注入 metadata 标签 (对照原版 0808.js:1222)
    content = s.metadata_tag() + s.body
    # 记录已加载
    if not hasattr(ctx, "loaded_skills"):
        ctx.loaded_skills = set()
    ctx.loaded_skills.add(skill)  # type: ignore[attr-defined]
    return content


SEARCH_SKILLS_SPEC = {
    "name": "search_skills",
    "description": (
        "Search the skill catalog by keyword. Returns matching skills with relevance scores. "
        "Use to discover skills for tasks like figure creation, paper writing, PDF reading."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Max results (default 4)"},
        },
        "required": ["query"],
    },
}

SKILL_SPEC = {
    "name": "skill",
    "description": (
        "Load and activate a skill by exact name. Returns the skill's instructions to follow. "
        "Use search_skills first if you don't know the exact name."
    ),
    "parameters": {
        "type": "object",
        "properties": {"skill": {"type": "string", "description": "Exact skill name"}},
        "required": ["skill"],
    },
}
