"""Rolling Compact 引擎主入口。



简化版 (同步阻塞,无并发 fork):
原版 fork 是异步并发的 (dispatch 后后台跑,下一轮再应用)。
本版简化为同步: 每轮 check 时若有 chunk 就当场压缩并应用。
这样失去"非压力下不阻塞"的优化,但逻辑简单可靠,长任务测试足够。
后续可升级为 fork 模式。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from axiom_core.llm.base import LLMClient
from axiom_core.llm.messages import Message

from .chunk import _chunk_key, pick_next_chunk
from .constants import (
    COMPACTION_TRIGGER_RATIO,
    HARD_WALL_RATIO,
    MAX_FORK_FAILURES,
    compute_ka,
)
from .summarizer import build_summary_message, summarize_chunk
from .token_est import estimate_messages_total

if TYPE_CHECKING:
    from .state import RollingCompactState

logger = logging.getLogger(__name__)


class CompactResult:
    """check_rolling_compact 的结果。"""

    def __init__(self, rtype: str, **kw):
        self.type = rtype  # idle / applied / dispatched / block_applied
        for k, v in kw.items():
            setattr(self, k, v)


async def check_rolling_compact(
    messages: list[Message],
    rc_state: RollingCompactState,
    llm: LLMClient,
    *,
    context_window: int,
    ka_ratio: float = 0.2,
    frame_id: str | None = None,
    applied_summary_uuids: set[str] | None = None,
    system_tokens: int = 0,
    model: str | None = None,
) -> CompactResult:
    """Rolling Compact 主入口。

    简化流程 (同步):
    1. 算 budget / ka
    2. 估算当前总 token
    3. 若超 hard-wall (budget*0.9) 或有 overflow → 压力模式
    4. pick_next_chunk (L1 优先, 失败升级 L2)
    5. 若有 chunk → summarize_chunk → 应用 (append summary, 追加到 applied)
    6. 循环直到无 chunk 或不超墙
    """
    applied = applied_summary_uuids or set()
    budget = context_window
    ka = compute_ka(budget, ka_ratio)
    pressure = rc_state.known_overflow is not None or rc_state.wall_pressure

    total_applied = 0
    tokens_freed = 0

    for _ in range(8):  # 最多 8 轮 (
        total = estimate_messages_total(messages, system_tokens=system_tokens)
        wall = int(budget * HARD_WALL_RATIO)
        soft = int(budget * COMPACTION_TRIGGER_RATIO)
        # 超 hard-wall → 压力模式 (L1 不判剩余,直接触发)
        if total >= wall:
            pressure = True
            rc_state.wall_pressure = True
        # 超软触发 (75%) → 开始 L1 压缩 (不等硬墙)
        elif total >= soft:
            pressure = True  # 标记压力, 但不设 wall_pressure (L1 仍会判剩余预算)
        elif total < soft and not pressure:
            break  # 未超软触发且无压力,无需压缩

        chunk = pick_next_chunk(messages, rc_state, budget, ka, applied, frame_id)
        if chunk is None:
            # L1/L2 都无法分块 → 尝试微压缩 (截断超长 tool_result, 不需要 LLM 调用)
            if pressure:
                truncated = _microcompact(messages)
                if truncated > 0:
                    logger.info("microcompact: truncated %d tool_result blocks", truncated)
                    continue
            break

        # 失败计数上限检查
        if frame_id:
            key = f"{frame_id}:{chunk.level}"
            rec = rc_state.fail_table.get(key)
            if (
                rec
                and rec.get("chunk_key") == _chunk_key(chunk)
                and rec.get("fail_count", 0) >= MAX_FORK_FAILURES
            ):
                rc_state.consecutive_fork_failures = MAX_FORK_FAILURES
                break

        # 压缩
        rs = await summarize_chunk(llm, messages, chunk, model=model)

        if rs is None:
            # 失败: bump 失败计数
            if frame_id:
                key = f"{frame_id}:{chunk.level}"
                rec = rc_state.fail_table.get(key)
                ckey = _chunk_key(chunk)
                if rec and rec.get("chunk_key") == ckey:
                    rc_state.fail_table[key] = {
                        "chunk_key": ckey,
                        "fail_count": rec.get("fail_count", 0) + 1,
                    }
                else:
                    rc_state.fail_table[key] = {"chunk_key": ckey, "fail_count": 1}
            rc_state.consecutive_fork_failures += 1
            break

        # 成功: append summary 消息 + 标记 applied
        sum_msg = build_summary_message(rs)
        messages.append(sum_msg)
        sum_uuid = getattr(sum_msg, "uuid", None)
        if sum_uuid:
            applied.add(sum_uuid)
        total_applied += 1
        tokens_freed += rs.tokens_freed
        rc_state.consecutive_fork_failures = 0  # 成功重置
        # 清失败计数
        if frame_id:
            rc_state.fail_table.pop(f"{frame_id}:{chunk.level}", None)

        # 压力释放后继续循环
        if total - tokens_freed < int(budget * HARD_WALL_RATIO):
            pressure = False
            rc_state.known_overflow = None

    if total_applied > 0:
        return CompactResult("applied", count=total_applied, tokens_freed=tokens_freed)
    return CompactResult("idle")


def abort_rolling_compact(rc_state: RollingCompactState, reason: str = "") -> bool:
    """中止 Rolling Compact。

    清状态,不动 messages。
    """
    aborted = False
    if rc_state.pending is not None:
        rc_state.pending = None
        aborted = True
    rc_state.known_overflow = None
    rc_state.wall_pressure = False
    rc_state.ptl_attempts = 0
    return aborted


def note_overflow(rc_state: RollingCompactState, overflow_tokens: int) -> bool:
    """记录 overflow。

    Returns: 是否还能重试 (< PTL_RETRY_CAP)。
    """
    from .constants import PTL_RETRY_CAP

    rc_state.known_overflow = overflow_tokens
    rc_state.ptl_attempts += 1
    return rc_state.ptl_attempts < PTL_RETRY_CAP


def _microcompact(messages: list[Message]) -> int:
    """微压缩: 原地截断超长的 tool_result 内容。

    不需要 LLM 调用 — 只是压力释放阀。
    截断超过 8000 字符的 tool_result, 保留前 4000 + [truncated] 标记。
    """
    from axiom_core.llm.messages import ToolResultBlock

    MAX_LEN = 8000
    KEEP_LEN = 4000
    count = 0

    for msg in messages:
        if not isinstance(msg.content, list):
            continue
        for i, block in enumerate(msg.content):
            if not isinstance(block, ToolResultBlock):
                continue
            if not isinstance(block.content, str):
                continue
            if len(block.content) <= MAX_LEN:
                continue
            # 截断
            truncated = block.content[:KEEP_LEN] + "\n...[truncated by microcompact]"
            msg.content[i] = ToolResultBlock(
                tool_use_id=block.tool_use_id,
                content=truncated,
                is_error=block.is_error,
            )
            count += 1

    return count
