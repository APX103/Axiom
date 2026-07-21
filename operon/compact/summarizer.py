"""Rolling Compact summarizer — 实际压缩 chunk 成摘要。



简化版 (保留核心,去掉可选优化):
- 保留: LLM 压缩 + 压缩门(final<=chunk/3) + 重试(GATE_MAX_RETRIES=3) + 退化检测
- 简化: 去掉 schema summarizer / literals / second-pass tighten / mechanical fallback
- 失败时返回 None (上层升级 L2 或破坏性)
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from operon.llm.base import LLMClient
from operon.llm.messages import Message, Role, TextBlock

from .chunk import ChunkRange
from .constants import (
    CHARS_PER_TOKEN,
    COMPRESSION_GATE_DIVISOR,
    DEGENERATE_DRAFT_RATIO,
    GATE_MAX_RETRIES,
    OUTPUT_CEILING,
    degenerate_min,
    output_ceiling_for,
)
from .state import RollingSummaryMeta, make_summary_id

if TYPE_CHECKING:
    pass

SUMMARIZER_SYSTEM = (
    "You are a conversation summarizer. Summarize the given conversation chunk into a concise "
    "summary that preserves: key decisions, results, file/artifact names, "
    "identifiers, numeric values, "
    "and any open tasks. Be factual and specific. Do not add information not present. "
    "Do not call any tools. Output only the summary text."
)


def _build_chunk_messages(chunk: list[Message], target_tokens: int) -> list[Message]:
    """构建给 summarizer 的消息: chunk 内容 + 压缩指令。"""
    # 把 chunk 序列化成文本
    parts = []
    for m in chunk:
        role = m.role.value
        if isinstance(m.content, str):
            text = m.content
        else:
            text = _serialize_blocks(m.content)
        parts.append(f"[{role}] {text}")
    chunk_text = "\n\n".join(parts)

    instruction = (
        f"Summarize the following conversation chunk into approximately {target_tokens} tokens. "
        "Preserve all key facts, decisions, file names, and identifiers verbatim.\n\n"
        f"--- CHUNK ---\n{chunk_text}\n--- END CHUNK ---"
    )
    return [
        Message(role=Role.USER, content=instruction),
    ]


def _serialize_blocks(blocks) -> str:
    """序列化 content blocks 为文本。"""
    out = []
    for b in blocks:
        bdict = b if isinstance(b, dict) else json.loads(b.model_dump_json())
        t = bdict.get("type", "unknown")
        if t == "text":
            out.append(bdict.get("text", ""))
        elif t == "tool_use":
            out.append(
                f"[tool_use {bdict.get('name')}] "
                f"{json.dumps(bdict.get('input', {}), ensure_ascii=False)}"
            )
        elif t == "tool_result":
            c = bdict.get("content", "")
            out.append(
                "[tool_result] "
                f"{c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)}"
            )
        elif t == "thinking":
            out.append(f"[thinking] {bdict.get('thinking', '')}")
        else:
            out.append(f"[{t}] {json.dumps(bdict, ensure_ascii=False, default=str)}")
    return "\n".join(out)


async def summarize_chunk(
    llm: LLMClient,
    messages: list[Message],
    chunk_range: ChunkRange,
    *,
    model: str | None = None,
) -> RollingSummaryMeta | None:
    """压缩一个 chunk 成摘要。

    Returns:
        RollingSummaryMeta (成功) | None (失败)
    """
    # 取 chunk 消息
    uuid_to_idx = {
        getattr(m, "uuid", None): i for i, m in enumerate(messages) if getattr(m, "uuid", None)
    }

    if chunk_range.folds_uuids:  # L2
        chunk = [messages[uuid_to_idx[u]] for u in chunk_range.folds_uuids if u in uuid_to_idx]
    else:  # L1
        f = uuid_to_idx.get(chunk_range.from_uuid)
        t = uuid_to_idx.get(chunk_range.to_uuid)
        if f is None or t is None:
            return None
        lo, hi = min(f, t), max(f, t)
        chunk = messages[lo : hi + 1]

    if not chunk:
        return None

    chunk_tokens = chunk_range.chunk_tokens
    target = chunk_tokens // 3  # 压缩目标
    max_out = output_ceiling_for(chunk_tokens, OUTPUT_CEILING)

    best_text: str | None = None
    best_tokens = 0

    # attempt 在循环结束后用于 RollingSummaryMeta.attempt, 故非未用变量。
    for attempt in range(1, GATE_MAX_RETRIES + 1):  # noqa: B007
        try:
            chunk_msgs = _build_chunk_messages(chunk, target)
            resp = await llm.chat(
                chunk_msgs,
                system=SUMMARIZER_SYSTEM,
                model=model,
                max_tokens=max_out,
                temperature=0,
            )
            raw = ""
            for b in resp.content:
                if hasattr(b, "text"):
                    raw += b.text
            if not raw.strip():
                continue
        except Exception:
            continue

        final_tokens = max(1, len(raw) // CHARS_PER_TOKEN)  # max(1,...) 防止空摘要 token=0
        gate = chunk_tokens // COMPRESSION_GATE_DIVISOR  # chunk/3

        # 退化检测
        dmin = degenerate_min(chunk_tokens)
        if (best_text and final_tokens < best_tokens * DEGENERATE_DRAFT_RATIO
                and final_tokens < dmin):
            raw = best_text
            final_tokens = best_tokens

        # 压缩门通过
        if final_tokens <= gate and final_tokens > 0:
            best_text = raw
            best_tokens = final_tokens
            break

        # 没通过,记最佳草稿继续重试
        if not best_text or final_tokens > best_tokens:
            best_text = raw
            best_tokens = final_tokens

    if not best_text or best_tokens == 0:
        return None

    # 净释放必须 > 0
    tokens_freed = chunk_tokens - best_tokens
    if tokens_freed <= 0:
        return None

    sum_uuid = str(uuid.uuid4())
    rs = RollingSummaryMeta(
        id=make_summary_id(sum_uuid),
        text=best_text,
        from_uuid=chunk_range.from_uuid,
        to_uuid=chunk_range.to_uuid,
        tokens_freed=tokens_freed,
        level=chunk_range.level,
        folds_uuids=chunk_range.folds_uuids or [],
        compression_ratio=chunk_tokens / max(1, best_tokens),
        attempt=attempt,
    )
    return rs


def build_summary_message(rs: RollingSummaryMeta) -> Message:
    """构建 summary 载体消息。

    存储形式: role=user, content=[TextBlock("[rolling-summary ID]")], 带 rolling_summary 元数据。
    """
    return Message(
        role=Role.USER,
        content=[TextBlock(text=f"[rolling-summary {rs.id}]")],
        **{
            "uuid": str(uuid.uuid4()),
            "rolling_summary": rs,
        },
    )
