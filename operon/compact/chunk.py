"""Rolling Compact chunk 选择 (L1/L2 触发)。


"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from operon.llm.messages import Message

from .constants import KB_RATIO, L2_PREFIX_BUDGET_RATIO, MIN_CHUNK_TOKENS
from .state import RollingSummaryMeta
from .token_est import estimate_message_tokens


@dataclass
class ChunkRange:
    """一个待压缩的 chunk。"""

    from_uuid: str
    to_uuid: str
    chunk_tokens: int
    level: int  # 1 (L1) | 2 (L2)
    folds_uuids: list[str] | None = None  # 仅 L2
    absorbed_from_key: str | None = None  # 吸收了失败 chunk


def _is_summary(m: Message) -> bool:
    return getattr(m, "rolling_summary", None) is not None


def _is_task_boundary(m: Message) -> bool:
    return getattr(m, "task_boundary", None) is not None


def _has_server_input(m: Message) -> bool:
    return bool(getattr(m, "server_input_tokens", None))


def _uuid(m: Message) -> str | None:
    return getattr(m, "uuid", None)


def _visible_messages(messages: list[Message], applied: set[str]) -> list[tuple[int, Message]]:
    """投影后的可见消息 (drop 掉被生效 summary 覆盖的)。"""
    from .projection import compute_projection

    proj = compute_projection(messages, applied)
    drop = proj["drop"]
    return [(i, m) for i, m in enumerate(messages) if not (_uuid(m) and _uuid(m) in drop)]


def should_trigger_l1(
    messages: list[Message],
    ka: int,
    applied: set[str],
    *,
    pressure: bool = False,
    exclude_to: str | None = None,
) -> ChunkRange | None:
    """L1 触发判定。

    非压力: 剩余预算 < ka * KB_RATIO(0.7) 才触发。
    压力: 只要存在 >= MIN_CHUNK_TOKENS 的 chunk 立即触发。
    """
    visible = _visible_messages(messages, applied)
    if len(visible) < 2:
        return None

    # 总可见 token (用于算 chunk 之后的剩余预算)
    total_tokens = sum(estimate_message_tokens(m) for _, m in visible)
    # 非压力下: 总量未达 ka 时不压缩 (对话还不够大)
    if not pressure and total_tokens < ka:
        return None

    # 从头找 chunk: 跳过有 server input / task boundary / 无 uuid 的
    x = 0
    K = 0  # chunk 起点之前累积的 token
    start_uuid: str | None = None

    for _ in range(32):  # 最多 32 次 chunk 提取
        # 跳过不可折叠的
        while x < len(visible):
            _, v = visible[x]
            if _has_server_input(v) or not _uuid(v):
                if _has_server_input(v):
                    start_uuid = None
                    K = 0
                x += 1
                continue
            if _is_task_boundary(v) and x + 1 < len(visible):
                _, nxt = visible[x + 1]
                if _has_server_input(nxt) or _is_task_boundary(nxt):
                    x += 1
                    continue
            break
        if x >= len(visible):
            return None

        # 从 x 生长 chunk 到 >= ka token
        chunk_tokens = 0
        end = x
        reached_end = True
        for j in range(x, len(visible)):
            _, m = visible[j]
            chunk_tokens += estimate_message_tokens(m)
            end = j
            if chunk_tokens >= ka:
                reached_end = False
                break
        # 若扫到末尾仍不够 ka 且 chunk < MIN_CHUNK → 无可用 chunk
        if reached_end and chunk_tokens < MIN_CHUNK_TOKENS:
            return None
        # 找 chunk 末尾的有 uuid 消息
        to_u: str | None = None
        while end > x:
            tu = _uuid(visible[end][1])
            if tu:
                to_u = tu
                break
            end -= 1
        if not to_u:
            return None

        excluded = exclude_to is not None and to_u == exclude_to
        # 触发判定
        if chunk_tokens >= MIN_CHUNK_TOKENS and not excluded:
            # chunk 之后的剩余预算 = total - (K + chunk_tokens)
            after = total_tokens - (K + chunk_tokens)
            # 非压力下: 剩余 < ka*0.7 才触发 (剩余少了就该压,腾空间)
            #
            if not pressure and after >= ka * KB_RATIO:
                return None
            from_u = start_uuid or _uuid(visible[x][1])
            if not from_u or not to_u:
                return None
            return ChunkRange(
                from_uuid=from_u,
                to_uuid=to_u,
                chunk_tokens=chunk_tokens + K,
                level=1,
            )
        # chunk 不够大: 累积,继续往后
        if start_uuid is None:
            start_uuid = _uuid(visible[x][1])
        K += chunk_tokens
        x = end + 1
    return None


def should_trigger_l2(
    messages: list[Message],
    applied: set[str],
    context_window: int,
    ka: int,
    *,
    pressure: bool = False,
) -> ChunkRange | None:
    """L2 触发判定。

    条件: 头部 >=3 条 L1 summary 且头部总 token >= max(8192, ka*0.4)。
    """
    visible = _visible_messages(messages, applied)
    if not visible:
        return None

    # 收集头部: summary + task_boundary + 小块原始消息 (遇大块停止)
    head: list[tuple[int, Message]] = []
    l1_count = 0
    non_summary_run = 0
    for i, m in visible:
        if _is_summary(m):
            head.append((i, m))
            l1_count += 1
            non_summary_run = 0
        elif _is_task_boundary(m):
            head.append((i, m))
            non_summary_run = 0
        else:
            non_summary_run += estimate_message_tokens(m)
            if non_summary_run >= MIN_CHUNK_TOKENS:
                break
            head.append((i, m))

    # 砍掉尾部非 summary
    while head and not _is_summary(head[-1][1]):
        head.pop()

    # 条件 1: 至少 3 条 summary
    if l1_count < 3:
        return None

    # 条件 2 (非压力): 头部总 token >= max(8192, ka*0.4)
    if not pressure:
        M = 0
        for _, s in head:
            rs: RollingSummaryMeta | None = getattr(s, "rolling_summary", None)
            if rs:
                M += len(rs.text) // 4  # CHARS_PER_TOKEN
            else:
                M += estimate_message_tokens(s)
        threshold = max(MIN_CHUNK_TOKENS * 2, int(ka * L2_PREFIX_BUDGET_RATIO))
        if M < threshold:
            return None

    # 折叠范围: head[0] 到第 l1_count 个 summary
    seen = 0
    Q = len(head) - 1
    for idx, (_i, m) in enumerate(head):
        if _is_summary(m):
            seen += 1
            if seen == l1_count:
                Q = idx
                break

    folds_uuids = [_uuid(head[j][1]) for j in range(Q + 1) if _uuid(head[j][1])]
    total_tokens = sum(estimate_message_tokens(head[j][1]) for j in range(Q + 1))
    if total_tokens == 0 or not folds_uuids:
        return None

    return ChunkRange(
        from_uuid=folds_uuids[0],
        to_uuid=folds_uuids[-1],
        chunk_tokens=total_tokens,
        level=2,
        folds_uuids=folds_uuids,
    )


def pick_next_chunk(
    messages: list[Message],
    rc_state: Any,
    context_window: int,
    ka: int,
    applied: set[str],
    frame_id: str | None = None,
) -> ChunkRange | None:
    """选下一个 chunk。

    L1 优先; 同 chunk 失败 >= MAX_FORK_FAILURES 转向 L2。
    """
    from .constants import MAX_FORK_FAILURES

    pressure = rc_state.known_overflow is not None or rc_state.wall_pressure

    l1 = should_trigger_l1(messages, ka, applied, pressure=pressure)
    if l1:
        # 检查失败计数
        if frame_id:
            key = f"{frame_id}:1"
            rec = rc_state.fail_table.get(key)
            ckey = _chunk_key(l1)
            if (
                rec
                and rec.get("chunk_key") == ckey
                and rec.get("fail_count", 0) >= MAX_FORK_FAILURES
            ):
                # 升级 L2
                l2 = should_trigger_l2(messages, applied, context_window, ka, pressure=pressure)
                if l2:
                    return l2
                # chunk 小: 尝试吸收相邻
                if (
                    l1.chunk_tokens < 2 * MIN_CHUNK_TOKENS
                    and rec.get("fail_count") == MAX_FORK_FAILURES
                ):
                    alt = should_trigger_l1(
                        messages, ka, applied, pressure=pressure, exclude_to=l1.to_uuid
                    )
                    if alt and alt.from_uuid == l1.from_uuid:
                        alt.absorbed_from_key = ckey
                        return alt
        return l1
    return should_trigger_l2(messages, applied, context_window, ka, pressure=pressure)


def _chunk_key(chunk: ChunkRange) -> str:
    """chunk 的唯一 key (用于失败计数)。"""
    return f"{chunk.from_uuid}:{chunk.to_uuid}:{chunk.level}"
