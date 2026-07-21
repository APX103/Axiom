"""记忆工具: read_memory / write_memory / search_memory。


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
        "entry. Optionally set entity_type (claim/evidence/citation/tool_use/note) "
        "and meta (structured fields). Skip transient task state."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entity": {
                "type": "string",
                "description": (
                    "Default entity for appends: profile / project / frame "
                    "(default: project)."
                ),
            },
            "append": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "body": {"type": "string", "description": "The fact (≤1000 chars)."},
                        "entity": {
                            "type": "string",
                            "description": "Override entity for this entry.",
                        },
                        "evidence": {
                            "type": "string",
                            "description": "stated / observed / inferred (default: inferred).",
                        },
                        "entity_type": {
                            "type": "string",
                            "description": (
                                "Semantic type: claim / evidence / citation / "
                                "tool_use / note (default: note)."
                            ),
                        },
                        "meta": {
                            "type": "object",
                            "description": (
                                "Structured fields per entity_type. claim: "
                                "{subject,predicate,object}; citation: "
                                "{doi,title,authors,year}; etc."
                            ),
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Confidence 0-1 (default: 0.5).",
                        },
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
                        "meta": {
                            "type": "object",
                            "description": "Optional structured fields update.",
                        },
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
    """读取某层所有记忆。Layer A: 输出含 entity_type 标签。"""
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
        et = e.get("entity_type", "note")
        lines.append(f"[{age}] [{et}] [{e.get('evidence', '')}] {e['body']}  [{e['id']}]")
    return "\n".join(lines)


async def write_memory(
    ctx: ToolContext,
    *,
    entity: str = "project",
    append: list[dict] | None = None,
    replace: list[dict] | None = None,
    remove: list[str] | None = None,
) -> str:
    """写入记忆。Layer A: 支持 entity_type / meta / confidence。"""
    if ctx.memory_store is None:
        return "Memory not available."
    count = 0
    for item in (append or [])[:20]:
        ent = item.get("entity", entity)
        body = item.get("body", "")
        ev = item.get("evidence", "inferred")
        if body:
            await ctx.memory_store.append(
                ent, body,
                evidence=ev, origin="user_stated",
                frame_id=ctx.frame.id if ent == "frame" else None,
                # Layer A 新字段
                scope=ent,
                entity_type=item.get("entity_type", "note"),
                meta=item.get("meta") if isinstance(item.get("meta"), dict) else None,
                session_id=ctx.session_id,
                confidence=item.get("confidence", 0.5),
                # Layer A.5
                project_id=getattr(ctx, "project_id", None),
            )
            count += 1
    for item in (replace or [])[:20]:
        meta = item.get("meta")
        await ctx.memory_store.replace(
            item["id"], item["body"],
            meta=meta if isinstance(meta, dict) else None,
        )
        count += 1
    for mid in (remove or [])[:20]:
        await ctx.memory_store.remove(mid)
        count += 1
    return f"Memory updated: {count} operations."


async def search_memory(ctx: ToolContext, *, query: str) -> str:
    """搜索全部记忆。Layer A: 输出含 scope + entity_type 标签。"""
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
        scope = r.get("scope", r.get("entity", ""))
        et = r.get("entity_type", "note")
        lines.append(
            f"[{scope}] [{et}] [{r.get('evidence', '')}] {r['body']}  [{r['id']}]"
        )
    return "\n".join(lines)
