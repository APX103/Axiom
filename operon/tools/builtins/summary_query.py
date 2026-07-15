"""summary_query 工具: 让 agent 查询被 Rolling Compact 折叠掉的原始消息内容。



摘要是有损的 — agent 可能需要被压缩掉的原始 UUID、数字、文件内容等。
此工具根据 summary 的 from_uuid/to_uuid 重建原始消息块, 做一次 LLM 问答。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SUMMARY_QUERY_SPEC = {
    "name": "summary_query",
    "description": (
        "Query the original (pre-compaction) content behind a rolling summary. "
        "Use when you need an exact UUID, SHA, numeric value, file content, or "
        "error message that a <summary> block elided. Pass the summary id and "
        "your question. Returns the answer from the original conversation chunk. "
        "If you are about to write an identifier or metric into a deliverable "
        "and the value comes from compacted context, query first — do not "
        "reconstruct it from memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "The summary id (from <summary id=\"...\">).",
            },
            "question": {
                "type": "string",
                "description": "What you need from the original conversation.",
            },
        },
        "required": ["summary", "question"],
    },
}


async def summary_query(ctx: ToolContext, *, summary: str, question: str) -> str:
    """查询被折叠摘要覆盖的原始消息。

    从 frame.messages 中找到 summary 消息的 from_uuid/to_uuid 范围,
    重建原始消息块, 做一次 LLM 问答。
    """
    import json

    from operon.llm.messages import Message, Role

    # 找到对应的 summary 消息
    target_msg = None
    for m in ctx.frame.messages:
        rs = getattr(m, "rolling_summary", None)
        if rs and getattr(rs, "id", None) == summary:
            target_msg = m
            break

    if target_msg is None:
        return f"Summary '{summary}' not found. Available summaries: " + ", ".join(
            getattr(m, "rolling_summary", None) and getattr(m.rolling_summary, "id", "")
            for m in ctx.frame.messages
            if getattr(m, "rolling_summary", None)
        )

    rs = target_msg.rolling_summary
    from_uuid = getattr(rs, "from_uuid", None)
    to_uuid = getattr(rs, "to_uuid", None)

    if not from_uuid or not to_uuid:
        return f"Summary '{summary}' has no message range metadata."

    # 重建原始消息块 (from_uuid 到 to_uuid 之间的消息)
    chunk_msgs = []
    in_range = False
    for m in ctx.frame.messages:
        mu = getattr(m, "uuid", None)
        if mu == from_uuid:
            in_range = True
        if in_range:
            chunk_msgs.append(m)
        if mu == to_uuid:
            break

    if not chunk_msgs:
        return f"Original messages for summary '{summary}' are no longer available."

    # 序列化消息块为文本
    lines = []
    for m in chunk_msgs:
        role = m.role.value if hasattr(m.role, "value") else str(m.role)
        if isinstance(m.content, str):
            lines.append(f"[{role}] {m.content}")
        else:
            for b in m.content:
                if hasattr(b, "text"):
                    lines.append(f"[{role}] {b.text}")
                elif hasattr(b, "name"):
                    lines.append(f"[{role}] (tool_use: {b.name} input={json.dumps(b.input, ensure_ascii=False)[:200]})")
                elif hasattr(b, "tool_use_id"):
                    content_str = b.content if isinstance(b.content, str) else str(b.content)
                    lines.append(f"[{role}] (tool_result: {content_str[:200]})")

    chunk_text = "\n".join(lines)
    if len(chunk_text) > 50000:
        chunk_text = chunk_text[:50000] + "\n...[truncated]"

    # 做一次 LLM 问答
    system = (
        "You are answering a question about an archived conversation excerpt. "
        "Read the conversation below and answer the question concisely. "
        "If the answer is not in the excerpt, say NOT FOUND. "
        "Do not make up information."
    )
    user_msg = f"Question: {question}\n\n--- ARCHIVED CONVERSATION ---\n{chunk_text}\n--- END ---"

    try:
        # 优先用 host 对象的 LLM 客户端 (agent 运行时可用)
        if ctx.host is not None and hasattr(ctx.host, "_llm") and ctx.host._llm is not None:
            client = ctx.host._llm
        else:
            return f"LLM client not available. The original chunk had {len(chunk_msgs)} messages."

        resp = await client.chat(
            [Message(role=Role.USER, content=user_msg)],
            system=system,
            tools=None,
            model=None,
            max_tokens=2048,
        )
        # 提取文本
        for b in resp.content:
            if hasattr(b, "text"):
                return b.text
        return "No text in response."
    except Exception as e:
        logger.warning("summary_query LLM call failed: %s", e)
        return f"Query failed: {e}. The original chunk had {len(chunk_msgs)} messages."
