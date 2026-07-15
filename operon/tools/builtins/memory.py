"""记忆工具: read_memory / write_memory / search_memory。

对应原版: 0808.js:225-313 + 0865.js:2465-2919。
简化: 去掉 PI classifier, categories, supersede chain repair。
"""

from __future__ import annotations

import logging

from operon.tools.context import ToolContext

logger = logging.getLogger(__name__)

READ_MEMORY_SPEC = {
    "name": "read_memory",
    "description": (
        "Read all memory entries for one entity. Use 'profile' for user-global "
        "facts, 'project' for this project's decisions/constraints, 'frame' for "
        "this session's scratchpad notes. Use 'category:<name>' is not supported "
        "in this version."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entity": {
                "type": "string",
                "description": "profile / project / frame",
            },
        },
        "required": ["entity"],
    },
}

WRITE_MEMORY_SPEC = {
    "name": "write_memory",
    "description": (
        "Write durable facts to memory. Use 'append' to add new entries, "
        "'replace' to correct existing ones, 'remove' to delete. Write facts "
        "the moment you confirm them — one or two sentences each. Set entity "
        "(profile/project/frame) and evidence (stated/observed/inferred) per "
        "entry. Skip transient task state."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entity": {
                "type": "string",
                "description": "Default entity for appends: profile / project / frame (default: project).",
            },
            "append": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "body": {"type": "string", "description": "The fact (≤1000 chars)."},
                        "entity": {"type": "string", "description": "Override entity for this entry."},
                        "evidence": {"type": "string", "description": "stated / observed / inferred (default: inferred)."},
                    },
                    "required": ["body"],
                },
            },
            "replace": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["id", "body"],
                },
            },
            "remove": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Memory IDs to delete.",
            },
        },
    },
}

SEARCH_MEMORY_SPEC = {
    "name": "search_memory",
    "description": (
        "Search all memory entries (all entities) by keyword. Use when you "
        "suspect something was learned in a prior session but isn't in the "
        "current context. Returns ranked matches with body text and metadata."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language search query.",
            },
        },
        "required": ["query"],
    },
}


async def read_memory(ctx: ToolContext, *, entity: str) -> str:
    """读取某层所有记忆。"""
    if ctx.memory_store is None:
        return "Memory not available."
    entries = await ctx.memory_store.list_by_entity(
        entity, frame_id=ctx.frame.id if entity == "frame" else None,
    )
    if not entries:
        return f"No memories in entity '{entity}'."
    lines = [f"=== {entity} ({len(entries)} entries) ==="]
    for e in entries:
        age = e.get("created_at", "")[:10] if e.get("created_at") else ""
        lines.append(f"[{age}] [{e.get('evidence', '')}] {e['body']}  [{e['id']}]")
    return "\n".join(lines)


async def write_memory(
    ctx: ToolContext,
    *,
    entity: str = "project",
    append: list[dict] | None = None,
    replace: list[dict] | None = None,
    remove: list[str] | None = None,
) -> str:
    """写入记忆。"""
    if ctx.memory_store is None:
        return "Memory not available."
    count = 0
    for item in (append or [])[:20]:
        ent = item.get("entity", entity)
        body = item.get("body", "")
        ev = item.get("evidence", "inferred")
        if body:
            await ctx.memory_store.append(
                ent, body, evidence=ev, origin="user",
                frame_id=ctx.frame.id if ent == "frame" else None,
            )
            count += 1
    for item in (replace or [])[:20]:
        await ctx.memory_store.replace(item["id"], item["body"])
        count += 1
    for mid in (remove or [])[:20]:
        await ctx.memory_store.remove(mid)
        count += 1
    return f"Memory updated: {count} operations."


async def search_memory(ctx: ToolContext, *, query: str) -> str:
    """搜索全部记忆。"""
    if ctx.memory_store is None:
        return "Memory not available."
    # 用实时搜索 (不用缓存索引, 覆盖最新数据)
    from operon.memory.recall import BM25Index

    all_mems = await ctx.memory_store.list_all()
    idx = BM25Index()
    idx.build(all_mems)
    results = idx.search(query, limit=20)
    if not results:
        return f"No memories match '{query}'."
    lines = [f"=== search '{query}' ({len(results)} results) ==="]
    for r in results:
        lines.append(
            f"[{r.get('entity', '')}] [{r.get('evidence', '')}] {r['body']}  [{r['id']}]"
        )
    return "\n".join(lines)
