"""记忆提取: 每轮结束后从对话中提取持久事实。

对应原版: extractProjectMemoriesBackground (0873.js:519-647)。
简化: 用一次 LLM 调用, 返回 {append, replace, remove} 操作。
不做 PI classifier, 不做 literal-preservation repair。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from operon.llm.base import LLMClient
from operon.llm.messages import Message, Role

from .store import MemoryStore

logger = logging.getLogger(__name__)

EXTRACT_SYSTEM = """\
You are a memory-extraction pass for a research AI assistant. You review the \
conversation above and extract durable facts worth remembering for future sessions.

A colleague takes over this project next week and will never read this transcript. \
Write their handoff notes as memory operations by calling emit_memories ONCE with \
{append, replace, remove}.

Record what is now TRUE, DECIDED, PREFERRED, or CONSTANT because of this session:
- A decision and why it was made
- An insight or root cause that will still hold
- A preference or convention revealed by the user
- A non-obvious constant (URL, ID, config value, command)

Do NOT record:
- Transient task state or in-progress work
- Tool outputs, file contents, or code (those are in artifacts)
- Debugging fix recipes
- Timeline events (what happened at what time)

For each append, set entity to:
- "profile" — facts about the user (role, preferences, working style) that apply everywhere
- "project" — facts about this research project (decisions, constraints, vocabulary)
- "frame" — notes to your future self within this session only

For each append/replace, set evidence to:
- "stated" — the user told you directly
- "observed" — you saw it in a tool result or artifact
- "inferred" — your own conclusion

Reconcile against existing memories listed below — don't re-emit what's already \
there; use replace to correct, remove to delete obsolete.

Respond with ONLY a JSON object: {"append": [{"entity": "...", "body": "...", \
"evidence": "..."}], "replace": [{"id": "...", "body": "..."}], "remove": ["id1", "id2"]}\
"""

EXTRACT_MAX = 5


async def extract_memories(
    messages: list[Message],
    existing: list[dict[str, Any]],
    llm: LLMClient,
    *,
    frame_id: str | None = None,
    max_per_run: int = EXTRACT_MAX,
) -> dict[str, Any]:
    """从对话中提取记忆。返回 {append, replace, remove}。

    Args:
        messages: 本轮新增的对话消息
        existing: 现有记忆列表 (用于去重/更新)
        llm: LLM 客户端
        frame_id: 当前 frame id (frame 层记忆用)
    Returns:
        {"append": [...], "replace": [...], "remove": [...]}
    """
    # 序列化对话为文本 (跳过 tool_result, harness_notice)
    lines = []
    for m in messages:
        if getattr(m, "_harness_notice", False):
            continue
        role = m.role.value if hasattr(m.role, "value") else str(m.role)
        if isinstance(m.content, str):
            text = m.content
        else:
            parts = []
            for b in m.content:
                if hasattr(b, "text"):
                    parts.append(b.text)
                elif hasattr(b, "name"):
                    parts.append(f"(tool_use: {b.name})")
                # 跳过 tool_result
            text = " ".join(parts)
        if text.strip():
            lines.append(f"[{role}] {text}")

    transcript = "\n".join(lines)
    if len(transcript) > 8000:
        transcript = transcript[-8000:]

    if not transcript.strip():
        return {"append": [], "replace": [], "remove": []}

    # 现有记忆清单
    existing_text = ""
    if existing:
        existing_lines = []
        for m in existing[:50]:  # 最多 50 条
            existing_lines.append(f"- {m['id']} [{m['entity']}] [{m.get('evidence', '')}] {m['body']}")
        existing_text = "\n\nExisting memories:\n" + "\n".join(existing_lines)

    user_msg = (
        f"--- CONVERSATION ---\n{transcript}\n--- END ---"
        f"{existing_text}"
        f"\n\nMax {max_per_run} entries per array. Extract memories now."
    )

    try:
        resp = await llm.chat(
            [Message(role=Role.USER, content=user_msg)],
            system=EXTRACT_SYSTEM,
            tools=None,
            model=None,
            max_tokens=2048,
        )
        # 提取文本
        text = ""
        for b in resp.content:
            if hasattr(b, "text"):
                text += b.text
        # 解析 JSON
        text = text.strip()
        # 去掉可能的 markdown code fence
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        result = json.loads(text)
        return {
            "append": result.get("append", [])[:max_per_run],
            "replace": result.get("replace", [])[:max_per_run],
            "remove": result.get("remove", [])[:max_per_run],
        }
    except Exception as e:
        logger.warning("memory extraction failed: %s", e)
        return {"append": [], "replace": [], "remove": []}


async def apply_extraction(
    store: MemoryStore, ops: dict[str, Any], *, frame_id: str | None = None,
) -> int:
    """应用提取结果到存储。返回写入条数。"""
    count = 0
    for item in ops.get("append", []):
        entity = item.get("entity", "project")
        body = item.get("body", "")
        evidence = item.get("evidence", "inferred")
        if body:
            await store.append(
                entity, body, evidence=evidence, origin="extractor",
                frame_id=frame_id if entity == "frame" else None,
            )
            count += 1
    for item in ops.get("replace", []):
        mem_id = item.get("id", "")
        body = item.get("body", "")
        if mem_id and body:
            await store.replace(mem_id, body)
            count += 1
    for mem_id in ops.get("remove", []):
        if mem_id:
            await store.remove(mem_id)
            count += 1
    return count
