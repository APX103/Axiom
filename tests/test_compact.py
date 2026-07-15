"""Rolling Compact 测试。

对照原版 0848.js。用 mock LLM 验证:
1. token 估算
2. 投影 (drop/reposition)
3. L1/L2 触发条件
4. check_rolling_compact 端到端 (压缩 + 应用)
5. 常数对照原版
"""

from __future__ import annotations

import uuid

import pytest

from operon.compact.chunk import should_trigger_l1
from operon.compact.constants import (
    CHARS_PER_TOKEN,
    KA_FLOOR,
    KB_RATIO,
    L2_PREFIX_BUDGET_RATIO,
    MIN_CHUNK_TOKENS,
    OUTPUT_CEILING,
    compute_ka,
)
from operon.compact.engine import check_rolling_compact
from operon.compact.projection import (
    compute_projection,
    prepare_messages_for_llm,
    render_summary_block,
)
from operon.compact.state import (
    RollingSummaryMeta,
    make_summary_id,
    new_rolling_compact_state,
)
from operon.compact.token_est import estimate_message_tokens, estimate_messages_total
from operon.llm.base import LLMClient
from operon.llm.messages import LLMResponse, Message, Role, StopReason, TextBlock, TokenUsage

# ---------- 1. 常数对照原版 ----------


def test_constants_match_original():
    """常数严格对照原版 (见 docs/mapping.md)。"""
    assert CHARS_PER_TOKEN == 4  # d8
    assert KA_FLOOR == 50000  # mSz
    assert KB_RATIO == 0.7  # cSz
    assert L2_PREFIX_BUDGET_RATIO == 0.4  # lSz
    assert MIN_CHUNK_TOKENS == 4096  # Xx_
    assert OUTPUT_CEILING == 32000  # aSz


def test_compute_ka():
    """ka = max(KA_FLOOR, budget*ratio)。对应原版 T_G。"""
    assert compute_ka(500000, 0.2) == 100000  # 500000*0.2
    assert compute_ka(100000, 0.2) == 50000  # floor 到 KA_FLOOR
    assert compute_ka(50000, 0.2) == 50000  # 刚好 floor


# ---------- 2. token 估算 ----------


def test_estimate_text_message():
    """纯文本消息: len/CHARS_PER_TOKEN。"""
    m = Message(role=Role.USER, content="a" * 40)
    assert estimate_message_tokens(m) == 10  # 40/4


def test_estimate_empty():
    assert estimate_message_tokens(Message(role=Role.USER, content="")) == 0


def test_estimate_summary_message():
    """summary 消息: 用摘要文本长度。"""
    rs = RollingSummaryMeta(
        id="abc", text="x" * 40, from_uuid="a", to_uuid="b", tokens_freed=100, level=1
    )
    m = Message(role=Role.USER, content="[rolling-summary abc]", rolling_summary=rs)
    assert estimate_message_tokens(m) == 10  # 40/4


def test_estimate_total():
    msgs = [
        Message(role=Role.USER, content="a" * 40),  # 10
        Message(role=Role.ASSISTANT, content="b" * 80),  # 20
    ]
    assert estimate_messages_total(msgs, system_tokens=5) == 35


# ---------- 3. 投影 ----------


def test_projection_drops_covered():
    """applied summary 覆盖的原始消息被 drop。"""
    a = Message(role=Role.USER, content="msg a", uuid="u-a")
    b = Message(role=Role.ASSISTANT, content="msg b", uuid="u-b")
    c = Message(role=Role.USER, content="msg c", uuid="u-c")
    rs = RollingSummaryMeta(id="s1", text="summary", from_uuid="u-a", to_uuid="u-b", tokens_freed=50, level=1)
    s = Message(role=Role.USER, content="[rolling-summary s1]", uuid="u-s", rolling_summary=rs)
    messages = [a, b, c, s]

    proj = compute_projection(messages, {"u-s"})
    assert "u-a" in proj["drop"]
    assert "u-b" in proj["drop"]
    assert "u-c" not in proj["drop"]
    assert "u-s" in proj["repositioned"]


def test_projection_unapplied_summary_no_drop():
    """未 applied 的 summary 不 drop 任何东西。"""
    a = Message(role=Role.USER, content="msg a", uuid="u-a")
    b = Message(role=Role.ASSISTANT, content="msg b", uuid="u-b")
    rs = RollingSummaryMeta(id="s1", text="summary", from_uuid="u-a", to_uuid="u-b", tokens_freed=50, level=1)
    s = Message(role=Role.USER, content="[rolling-summary s1]", uuid="u-s", rolling_summary=rs)
    messages = [a, b, s]

    proj = compute_projection(messages, set())  # 未 applied
    assert proj["drop"] == set()
    assert proj["repositioned"] == set()


def test_prepare_messages_renders_summary():
    """投影后 summary 渲染成 assistant 块, 覆盖消息被删。"""
    a = Message(role=Role.USER, content="原始消息a", uuid="u-a")
    b = Message(role=Role.ASSISTANT, content="原始消息b", uuid="u-b")
    c = Message(role=Role.USER, content="新消息c", uuid="u-c")
    rs = RollingSummaryMeta(id="s1", text="这是摘要", from_uuid="u-a", to_uuid="u-b", tokens_freed=50, level=1)
    s = Message(role=Role.USER, content="[rolling-summary s1]", uuid="u-s", rolling_summary=rs)
    messages = [a, b, c, s]

    rendered = prepare_messages_for_llm(messages, {"u-s"})
    # 原始 a, b 被 drop; c 保留; summary 渲染成 assistant
    texts = []
    for m in rendered:
        c2 = m.content
        if isinstance(c2, str):
            texts.append(c2)
        elif isinstance(c2, list) and c2 and hasattr(c2[0], "text"):
            texts.append(c2[0].text)
    # 应含摘要和新消息,不含原始 a/b
    joined = "|".join(texts)
    assert "这是摘要" in joined  # summary 渲染
    assert "新消息c" in joined  # 新消息保留
    assert "原始消息a" not in joined  # 被 drop
    assert "原始消息b" not in joined


def test_render_summary_block_format():
    """summary 块格式: <summary id=X scope=Y>...</summary>。"""
    rs = RollingSummaryMeta(id="abc", text="摘要内容", from_uuid="a", to_uuid="b", tokens_freed=50, level=1)
    block = render_summary_block(rs)
    assert '<summary id=abc' in block
    assert 'scope=detail' in block
    assert '摘要内容' in block
    assert 'summary_query' in block  # hint


# ---------- 4. L1/L2 触发 ----------


def _make_long_conversation(n: int, tokens_per_msg: int = 2000) -> list[Message]:
    """造一个长对话: n 轮 user/assistant,每条约 tokens_per_msg token。"""
    msgs = []
    for i in range(n):
        msgs.append(Message(role=Role.USER, content="x" * (tokens_per_msg * 4), uuid=f"u-{i}"))
        msgs.append(Message(role=Role.ASSISTANT, content="y" * (tokens_per_msg * 4), uuid=f"a-{i}"))
    return msgs


def test_l1_triggers_when_over_budget():
    """超过 budget*0.7 应触发 L1。"""
    msgs = _make_long_conversation(30)  # 30 轮 * 2000 * 2 = 120000 tokens
    ka = compute_ka(500000, 0.2)  # 100000
    # 120000 > ka * 0.7 = 70000 → 应触发
    chunk = should_trigger_l1(msgs, ka, set(), pressure=False)
    assert chunk is not None
    assert chunk.level == 1


def test_l1_no_trigger_when_under_budget():
    """未超过 budget*0.7 不触发。"""
    msgs = _make_long_conversation(3)  # 3 轮 * 4000 = 12000 tokens,远小于 70000
    ka = compute_ka(500000, 0.2)
    chunk = should_trigger_l1(msgs, ka, set(), pressure=False)
    assert chunk is None


def test_l1_pressure_always_triggers():
    """压力模式下只要有 >= MIN_CHUNK_TOKENS 的 chunk 就触发。"""
    msgs = _make_long_conversation(5)  # 20000 tokens > MIN_CHUNK
    ka = compute_ka(500000, 0.2)
    chunk = should_trigger_l1(msgs, ka, set(), pressure=True)
    assert chunk is not None


# ---------- 5. check_rolling_compact 端到端 (mock LLM) ----------


class MockCompactLLM(LLMClient):
    """压缩用的 mock LLM: 返回固定长度的摘要。"""

    def __init__(self, summary_text: str = "压缩摘要: 关键信息保留。这是一个足够长的摘要以确保 token 数大于零。"):
        self.summary_text = summary_text
        self.calls = 0

    async def chat(self, messages, *, system=None, tools=None, model=None, max_tokens=8192, temperature=None, **kw):
        self.calls += 1
        return LLMResponse(
            content=[TextBlock(text=self.summary_text)],
            stop_reason=StopReason.END_TURN,
            model="mock",
            usage=TokenUsage(input_tokens=100, output_tokens=20),
        )

    def count_tokens(self, text):
        return len(text) // 4

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_check_rolling_compact_applies_summary():
    """超 budget 时 check_rolling_compact 应压缩并应用 summary。"""
    msgs = _make_long_conversation(40)  # ~160000 tokens,远超 500000*0.9=450000? 不,160000 < 450000
    # 用小 budget 触发
    rc = new_rolling_compact_state()
    llm = MockCompactLLM("短摘要")

    result = await check_rolling_compact(
        msgs,
        rc,
        llm,
        context_window=200000,  # 200000*0.9=180000, msgs~160000 不够
        ka_ratio=0.2,
        frame_id="test-frame",
    )
    # 160000 < 180000, 可能不触发 hard-wall; 用更小 budget
    # 重新用更激进的 budget
    msgs2 = _make_long_conversation(60)  # ~240000 tokens
    rc2 = new_rolling_compact_state()
    result2 = await check_rolling_compact(
        msgs2, rc2, llm, context_window=200000, ka_ratio=0.2, frame_id="test-frame2"
    )
    # 240000 > 180000 → 应触发
    assert result2.type == "applied"
    assert len(msgs2) > 120  # 原始 120 条 + summary
    # applied 集合应有 summary
    assert llm.calls > 0


@pytest.mark.asyncio
async def test_check_rolling_compact_idle_when_small():
    """小对话不压缩。"""
    msgs = _make_long_conversation(2)
    rc = new_rolling_compact_state()
    llm = MockCompactLLM()
    result = await check_rolling_compact(
        msgs, rc, llm, context_window=500000, ka_ratio=0.2, frame_id="small"
    )
    assert result.type == "idle"
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_check_rolling_compact_256k_context_window():
    """256K 模型 (如 StepFun step-3.7-flash) 应在 ~230K 触发 Compact,
    而非默认的 500K。验证 context_window 真实生效。

    150K tokens 对 500K budget 不触发 (150K < 450K wall),
    但对 256K budget 触发 (150K > 230K wall)。
    """
    msgs = _make_long_conversation(38)  # ~152000 tokens
    rc = new_rolling_compact_state()
    llm = MockCompactLLM("压缩摘要内容足够长确保token大于零")

    # 256K budget → wall = 230400, 152K < 230400 不触发
    r256 = await check_rolling_compact(
        msgs, rc, llm, context_window=256000, ka_ratio=0.2, frame_id="f256"
    )
    # 152K < 230K wall, 不触发 (idle)
    # 但 180K (45轮) 会触发
    msgs_big = _make_long_conversation(46)  # ~184000 > 230400? 184K < 230K 还是不够
    msgs_bigger = _make_long_conversation(60)  # ~240000 > 230400 触发
    rc2 = new_rolling_compact_state()
    r = await check_rolling_compact(
        msgs_bigger, rc2, llm, context_window=256000, ka_ratio=0.2, frame_id="f256b"
    )
    assert r.type == "applied", "240K 对 256K budget 应触发 (wall=230400)"
    assert llm.calls > 0


# ---------- 6. make_summary_id ----------


def test_make_summary_id():
    """短 id 从 uuid 生成。"""
    u = str(uuid.uuid4())
    sid = make_summary_id(u)
    assert len(sid) <= 10
    assert sid != u  # 是短形式
