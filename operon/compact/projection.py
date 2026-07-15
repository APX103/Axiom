"""Rolling Compact 投影。

对应原版: 0848.js:431-517 (hL_ computeRollingProjection) + 823-850 (t8O renderSummaryBlock)。

投影是 RC 的精髓: 原 chunk 消息永远留在 messages 里,
渲染给 LLM 时通过投影:
1. applied summary 覆盖的原始消息 → drop (从渲染列表删)
2. summary 消息 → reposition (移到 anchor 位置,即第一个被覆盖消息处)
3. summary 渲染成 assistant 的 <summary id=X>...</summary> 块

applied_summary_uuids 决定哪些 summary 当前生效。
"""

from __future__ import annotations

from operon.llm.messages import Message, Role, TextBlock

from .state import RollingSummaryMeta


def compute_projection(
    messages: list[Message], applied_summary_uuids: set[str]
) -> dict[str, set]:
    """计算投影: drop / repositioned 集合。

    对应原版 hL_ (0848.js:431-517)。

    Returns:
        {"drop": {uuid...}, "repositioned": {uuid...}}
    """
    drop: set[str] = set()
    repositioned: set[str] = set()
    uuid_to_idx: dict[str, int] = {}

    for i, m in enumerate(messages):
        u = getattr(m, "uuid", None)
        if u:
            uuid_to_idx[u] = i

    for m in messages:
        u = getattr(m, "uuid", None)
        rs: RollingSummaryMeta | None = getattr(m, "rolling_summary", None)
        if not u or rs is None:
            continue
        if u not in applied_summary_uuids:
            continue  # 未生效,不算覆盖

        # 确定这条 summary 覆盖的范围
        covered_uuids: list[str] = []
        if rs.folds_uuids:  # L2
            covered_uuids = list(rs.folds_uuids)
        elif rs.from_uuid and rs.to_uuid:  # L1
            f = uuid_to_idx.get(rs.from_uuid)
            t = uuid_to_idx.get(rs.to_uuid)
            if f is not None and t is not None:
                lo, hi = min(f, t), max(f, t)
                for j in range(lo, hi + 1):
                    cu = getattr(messages[j], "uuid", None)
                    if cu:
                        covered_uuids.append(cu)

        for cu in covered_uuids:
            # 不要 drop 掉其他生效 summary (L2 可能 fold 了 L1 summary)
            cu_msg = uuid_to_idx.get(cu)
            if cu_msg is not None:
                cu_rs = getattr(messages[cu_msg], "rolling_summary", None)
                if cu_rs is not None and cu in applied_summary_uuids:
                    continue  # 它也是生效 summary,保留
            drop.add(cu)
        repositioned.add(u)

    return {"drop": drop, "repositioned": repositioned}


def render_summary_block(rs: RollingSummaryMeta) -> str:
    """渲染 summary 为 LLM 可读的 <summary> 块。

    对应原版 t8O (0848.js:823-850)。
    存储是 user message,渲染成 assistant 的:
        <summary id=XXX scope=detail|overview>
        ...摘要正文...
        (exact values: summary_query(summary="XXX", question="..."))
        </summary>
    """
    scope = "overview" if rs.level >= 2 else "detail"
    hint = f'(exact values and specifics: use the summary_query tool with summary="{rs.id}", question="...")'
    return f'<summary id={rs.id} scope={scope}>\n{rs.text}\n\n{hint}\n</summary>'


def prepare_messages_for_llm(
    messages: list[Message],
    applied_summary_uuids: set[str],
    *,
    system: str | None = None,
) -> list[Message]:
    """投影后渲染给 LLM 的消息列表。

    对应原版 prepareMessagesForLlm (0858.js:2514) + applyRollingProjection。

    算法:
    1. 计算 drop/repositioned
    2. 每条 applied summary 计算其覆盖范围的起始 index (insert_at)
    3. 按原顺序遍历: 在 insert_at 处插入渲染后的 summary 块; 跳过 drop 和 summary 本身
    4. 末尾若以 assistant 结尾,补 [Continue.]
    """
    proj = compute_projection(messages, applied_summary_uuids)
    drop = proj["drop"]
    repositioned = proj["repositioned"]
    if not repositioned:
        return list(messages)

    uuid_to_idx = {getattr(m, "uuid", None): i for i, m in enumerate(messages) if getattr(m, "uuid", None)}

    # 每条 summary 的插入位置 = 它覆盖范围里第一条消息的 index
    insert_at: dict[str, int] = {}
    for m in messages:
        u = getattr(m, "uuid", None)
        rs = getattr(m, "rolling_summary", None)
        if u and rs and u in repositioned:
            if rs.folds_uuids:
                first_idx = next((uuid_to_idx.get(f) for f in rs.folds_uuids if f in uuid_to_idx), None)
            else:
                first_idx = uuid_to_idx.get(rs.from_uuid)
            insert_at[u] = first_idx if first_idx is not None else 0

    out: list[Message] = []
    inserted: set[str] = set()

    for i, m in enumerate(messages):
        u = getattr(m, "uuid", None)

        # 在此位置插入"以 i 为 insert_at"的所有 summary (渲染成 assistant 块)
        for su, at in insert_at.items():
            if at == i and su not in inserted:
                smsg = next((mm for mm in messages if getattr(mm, "uuid", None) == su), None)
                if smsg:
                    srs = getattr(smsg, "rolling_summary", None)
                    if srs:
                        out.append(
                            Message(
                                role=Role.ASSISTANT,
                                content=[TextBlock(text=render_summary_block(srs))],
                            )
                        )
                    inserted.add(su)

        # drop 的消息跳过
        if u and u in drop:
            continue
        # summary 消息本身跳过 (已 reposition 渲染)
        if u and u in repositioned:
            continue

        out.append(m)

    # 处理 insert_at 指向末尾之后的 summary (覆盖范围在最后)
    for su, at in insert_at.items():
        if su not in inserted and at >= len(messages):
            smsg = next((mm for mm in messages if getattr(mm, "uuid", None) == su), None)
            if smsg:
                srs = getattr(smsg, "rolling_summary", None)
                if srs:
                    out.append(Message(role=Role.ASSISTANT, content=[TextBlock(text=render_summary_block(srs))]))
                inserted.add(su)

    # 末尾若以 assistant 结尾,补 [Continue.]
    if out and out[-1].role == Role.ASSISTANT:
        out.append(Message(role=Role.USER, content="[Continue.]"))

    return out
