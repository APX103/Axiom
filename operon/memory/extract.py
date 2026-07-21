"""记忆提取: 每轮结束后从对话中提取持久事实。


Layer A 升级 (2026-07):
- prompt 重写, 让 LLM 同时输出 scope (作用域) 和 entity_type (语义类型)
- 4 类实体: claim / evidence / citation / tool_use / note (兜底)
- 输出含 confidence / meta / origin 多值
- 仍保持单次 LLM 调用 (不做 PI classifier, 不做多阶段)
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
- A scientific claim, hypothesis, or conclusion
- Evidence that supports or contradicts a claim
- A citation / reference worth remembering
- A tool invocation result that will be useful later
- A decision and why it was made
- An insight or root cause that will still hold
- A preference or convention revealed by the user
- A non-obvious constant (URL, ID, config value, command)

Do NOT record:
- Transient task state or in-progress work
- Verbatim file contents or code (those are in artifacts)
- Debugging fix recipes
- Timeline events (what happened at what time)

For each append, set BOTH scope and entity_type:

scope (visibility):
- "profile" — facts about the user (role, preferences, working style) that apply everywhere
- "project" — facts about this research project (decisions, constraints, vocabulary)
- "frame" — notes to your future self within this session only

entity_type (semantic class):
- "claim" — a scientific assertion / hypothesis / conclusion. meta: {subject, predicate, object}
- "evidence" — data or observation supporting/contradicting a claim. \
meta: {supports_claim_id (optional), direction: "supports"|"contradicts", observation}
- "citation" — a paper / dataset / external resource. \
meta: {doi (optional), title, authors (list), year}
- "tool_use" — a notable tool invocation and its outcome. \
meta: {tool_name, args_summary, result_summary}
- "note" — generic note that doesn't fit above types (default fallback)

For each append/replace, set:
- evidence: "stated" (user told you) / "observed" (tool result) / "inferred" (your conclusion)
- origin: "user_stated" / "agent_inferred" / "extractor" / "tool_observed"
- confidence: 0.0-1.0 how confident this memory is durable and correct
- meta: structured fields per entity_type (see above), or {} if not applicable

Reconcile against existing memories listed below — don't re-emit what's already \
there; use replace to correct, remove to delete obsolete.

Respond with ONLY a JSON object of this shape:
{"append": [{"scope": "...", "entity_type": "...", "body": "one-line human-readable summary", \
"evidence": "...", "origin": "...", "confidence": 0.8, "meta": {...}}], \
"replace": [{"id": "...", "body": "...", "meta": {...}}], \
"remove": ["id1", "id2"]}\
"""

EXTRACT_MAX = 5

# 合法的 scope / entity_type / origin 枚举 (解析时校验)
_VALID_SCOPES = {"profile", "project", "frame"}
_VALID_ENTITY_TYPES = {"claim", "evidence", "citation", "tool_use", "note"}
_VALID_ORIGINS = {"user_stated", "agent_inferred", "extractor", "tool_observed"}
_VALID_EVIDENCE = {"stated", "observed", "inferred"}


def _clamp_confidence(v: Any) -> float:
    """confidence 限制在 [0, 1]。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, f))


def _validate_append(item: dict[str, Any]) -> dict[str, Any]:
    """校验 + 规范化一条 append item。LLM 输出可能字段缺失/非法。"""
    scope = str(item.get("scope") or item.get("entity") or "project").strip()
    if scope not in _VALID_SCOPES:
        scope = "project"
    entity_type = str(item.get("entity_type") or "note").strip()
    if entity_type not in _VALID_ENTITY_TYPES:
        entity_type = "note"
    evidence = str(item.get("evidence") or "inferred").strip()
    if evidence not in _VALID_EVIDENCE:
        evidence = "inferred"
    origin = str(item.get("origin") or "extractor").strip()
    if origin not in _VALID_ORIGINS:
        origin = "extractor"
    meta = item.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    return {
        "scope": scope,
        "entity_type": entity_type,
        "body": str(item.get("body", "")).strip(),
        "evidence": evidence,
        "origin": origin,
        "confidence": _clamp_confidence(item.get("confidence", 0.5)),
        "meta": meta,
    }


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
            et = m.get("entity_type", "note")
            existing_lines.append(
                f"- {m['id']} [scope={m.get('scope', m.get('entity', ''))}] "
                f"[type={et}] [conf={m.get('confidence', 0.5):.2f}] {m['body']}"
            )
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
        # 校验 + 规范化每条 append
        raw_append = result.get("append", [])[:max_per_run]
        validated = [_validate_append(item) for item in raw_append if isinstance(item, dict)]
        # 过滤掉 body 为空的
        validated = [v for v in validated if v["body"]]
        return {
            "append": validated,
            "replace": result.get("replace", [])[:max_per_run],
            "remove": result.get("remove", [])[:max_per_run],
        }
    except Exception as e:
        logger.warning("memory extraction failed: %s", e)
        return {"append": [], "replace": [], "remove": []}


async def apply_extraction(
    store: MemoryStore,
    ops: dict[str, Any],
    *,
    frame_id: str | None = None,
    session_id: str | None = None,
    project_id: str | None = None,
) -> int:
    """应用提取结果到存储。返回写入条数。

    Layer A: 透传 entity_type/meta/session_id/confidence 到 store.append。
    Layer A.5: 透传 project_id (profile 层 store.append 内部会强制设回 None)。
    """
    count = 0
    for item in ops.get("append", []):
        scope = item.get("scope", "project")
        body = item.get("body", "")
        if not body:
            continue
        # 老字段 entity = scope (向后兼容), frame 层才填 frame_id
        await store.append(
            entity=scope,
            body=body,
            evidence=item.get("evidence", "inferred"),
            origin=item.get("origin", "extractor"),
            frame_id=frame_id if scope == "frame" else None,
            # Layer A 新字段
            scope=scope,
            entity_type=item.get("entity_type", "note"),
            meta=item.get("meta") or None,
            session_id=session_id,
            confidence=item.get("confidence", 0.5),
            # Layer A.5
            project_id=project_id,
        )
        count += 1
    for item in ops.get("replace", []):
        mem_id = item.get("id", "")
        body = item.get("body", "")
        if mem_id and body:
            meta = item.get("meta")
            await store.replace(mem_id, body, meta=meta if isinstance(meta, dict) else None)
            count += 1
    for mem_id in ops.get("remove", []):
        if mem_id:
            await store.remove(mem_id)
            count += 1
    return count
