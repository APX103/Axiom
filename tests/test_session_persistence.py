"""会话消息持久化测试 — 重点验证 thinking(推理过程)内容能完整存取。

背景: _serialize_content() 曾缺少 ThinkingBlock 分支, 导致扩展思考内容
在落库 / 活跃态 API 序列化时被静默丢弃。本测试覆盖:
- _serialize_content() 正确输出 thinking block
- thinking + text + tool_use 的混合 content 序列化后顺序与字段保持
- 落库 → 读回的完整 round-trip, thinking 不丢
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")

from operon.api.sessions import SessionManager, _serialize_content
from operon.llm.messages import Message, Role, TextBlock, ThinkingBlock, ToolUseBlock
from operon.tools.context import PlanState

# ---------- _serialize_content 单测 ----------


def test_serialize_thinking_block():
    """ThinkingBlock 序列化为 {"type": "thinking", "thinking": ...}。"""
    content = [ThinkingBlock(thinking="让我想想...")]
    out = _serialize_content(content)
    assert out == [{"type": "thinking", "thinking": "让我想想..."}]


def test_serialize_thinking_before_text_preserves_order():
    """thinking 排在 text 之前 (与流式聚合器 finalize() 的块顺序一致), 序列化后顺序保持。"""
    content = [
        ThinkingBlock(thinking="先推理一下"),
        TextBlock(text="答案是 42"),
        ToolUseBlock(id="toolu_1", name="calc", input={"x": 1}),
    ]
    out = _serialize_content(content)
    assert [b["type"] for b in out] == ["thinking", "text", "tool_use"]
    assert out[0] == {"type": "thinking", "thinking": "先推理一下"}
    assert out[1] == {"type": "text", "text": "答案是 42"}


def test_serialize_plain_string_passthrough():
    """纯文本 content 原样返回 (兼容 str 分支)。"""
    assert _serialize_content("你好") == "你好"


# ---------- DB round-trip ----------


@pytest.fixture
async def manager(tmp_path: Path) -> SessionManager:
    """带 SQLite 的 SessionManager (内存态为空, 仅测落库/读回)。"""
    from operon.db.session import init_engine, session_factory

    engine = await init_engine(f"sqlite:///{tmp_path / 'sessions.db'}")
    factory = session_factory(engine)
    mgr = SessionManager(db_session_factory=factory)
    # session_messages.session_id 外键引用 sessions.id, 先插一条 session 记录兜底
    from operon.db.schema import SessionRecord

    async with factory() as db:
        db.add(
            SessionRecord(
                id="sess-thinking-1",
                title=None,
                workspace=str(tmp_path),
                model="test-model",
                plan_mode=False,
                status="active",
            )
        )
        db.add(
            SessionRecord(
                id="sess-plain-1",
                title=None,
                workspace=str(tmp_path),
                model="test-model",
                plan_mode=False,
                status="active",
            )
        )
        db.add(
            SessionRecord(
                id="sess-harness",
                title=None,
                workspace=str(tmp_path),
                model="test-model",
                plan_mode=False,
                status="active",
            )
        )
        await db.commit()
    return mgr


@pytest.mark.asyncio
async def test_db_roundtrip_preserves_thinking(manager: SessionManager):
    """带 thinking 的 assistant 消息落库后读回, thinking 块仍在。"""
    msg = Message(
        role=Role.ASSISTANT,
        content=[
            ThinkingBlock(thinking="逐步推导: 1+1=2"),
            TextBlock(text="结果是 2"),
        ],
    )
    serialized = [
        {
            "role": msg.role.value,
            "content": _serialize_content(msg.content),
            "harness_notice": getattr(msg, "_harness_notice", False) or None,
        }
    ]

    sid = "sess-thinking-1"
    await manager._db_save_messages(sid, serialized)

    loaded = await manager._db_load_messages(sid)
    assert len(loaded) == 1
    content = loaded[0]["content"]
    # thinking 块必须存活到 DB 读回
    assert isinstance(content, list)
    assert any(
        isinstance(b, dict) and b.get("type") == "thinking"
        and b.get("thinking") == "逐步推导: 1+1=2"
        for b in content
    ), f"thinking block missing after round-trip: {content}"
    # text 块也在
    assert any(
        isinstance(b, dict) and b.get("type") == "text" and b.get("text") == "结果是 2"
        for b in content
    )


@pytest.mark.asyncio
async def test_db_roundtrip_without_thinking_unchanged(manager: SessionManager):
    """无 thinking 的普通消息落库读回仍正常 (回归保护)。"""
    msg = Message(role=Role.USER, content=[TextBlock(text="hi")])
    serialized = [
        {
            "role": msg.role.value,
            "content": _serialize_content(msg.content),
            "harness_notice": getattr(msg, "_harness_notice", False) or None,
        }
    ]
    sid = "sess-plain-1"
    await manager._db_save_messages(sid, serialized)

    loaded = await manager._db_load_messages(sid)
    assert loaded[0]["content"] == [{"type": "text", "text": "hi"}]


@pytest.mark.asyncio
async def test_db_roundtrip_preserves_harness_notice(manager: SessionManager):
    """harness_notice 消息 (memory 召回块/max_tokens 续传提示) 落库后标记不丢。

    背景: _db_load_messages 曾只取 role/content, 丢了 harness_notice, 导致
    刷新会话后 memory 块被前端当成普通 user 消息渲染成用户气泡。
    """
    serialized = [
        {
            "role": "user",
            "content": "[Memory]\n  [profile] [e] some fact",
            "harness_notice": True,
        },
        {
            "role": "assistant",
            "content": "你好",
            "harness_notice": None,
        },
    ]
    sid = "sess-harness"
    await manager._db_save_messages(sid, serialized)

    loaded = await manager._db_load_messages(sid)
    assert len(loaded) == 2
    # memory 块: harness_notice 标记存活, content 还原为原始文本
    assert loaded[0]["harness_notice"] is True
    assert loaded[0]["content"] == "[Memory]\n  [profile] [e] some fact"
    # 普通消息: 不带 harness_notice 标记
    assert "harness_notice" not in loaded[1]
    assert loaded[1]["content"] == "你好"


# ---------- 会话恢复 (restore) ----------


@pytest.mark.asyncio
async def test_restore_session_from_db_and_continue(tmp_path: Path, monkeypatch):
    """会话被淘汰/重启后, 能从 DB 恢复运行时状态并继续对话。

    覆盖核心路径:
    - 从 DB 读回 SessionRecord / messages / plan_data
    - 重建 LLM client + Session + ToolContext
    - 把历史消息直接拼回 ctx.frame.messages
    - 恢复 PlanState
    - 继续 run 时新消息排在已有上下文之后
    """
    import sys

    import operon.config  # noqa: F401
    import operon.settings  # noqa: F401
    from operon.config import ModelsConfig, ModelTier, RollingCompactConfig, Settings
    from operon.db.session import init_engine, session_factory
    from operon.llm.base import LLMClient
    from operon.llm.messages import LLMResponse, StopReason, TokenUsage
    from operon.settings import AppSettings

    class FakeLLM(LLMClient):
        async def chat(
            self,
            messages,
            *,
            system=None,
            tools=None,
            model=None,
            max_tokens=None,
            temperature=None,
            **kwargs,
        ):
            return LLMResponse(
                content=[TextBlock(text="继续回答")],
                stop_reason=StopReason.END_TURN,
                model="test-model",
                usage=TokenUsage(input_tokens=1, output_tokens=1),
            )

        async def chat_stream(self, **kwargs):
            raise NotImplementedError

        def count_tokens(self, text: str) -> int:
            return max(1, len(text) // 4)

        async def close(self) -> None:
            pass

    settings = Settings(
        data_dir=tmp_path,
        rolling_compact=RollingCompactConfig(enabled=False),
        models=ModelsConfig(
            large=ModelTier(
                model="test-model",
                base_url="http://localhost:9999",
                api_key="sk-test",
                context_window=256000,
            )
        ),
    )
    monkeypatch.setattr(
        sys.modules["operon.config"], "load_settings", lambda _path=None: settings
    )
    monkeypatch.setattr(
        sys.modules["operon.settings"], "get_app_settings", lambda _dd: AppSettings()
    )

    engine = await init_engine(f"sqlite:///{tmp_path / 'sessions.db'}")
    factory = session_factory(engine)
    mgr = SessionManager(db_session_factory=factory)

    # 创建会话并落库一些历史消息 + plan
    active = await mgr.create(
        llm=FakeLLM(),
        workspace=tmp_path / "workspaces" / "sess1",
        sid="sess1",
        model="test-model",
        data_dir=tmp_path,
    )
    active.ctx.frame.messages.append(Message(role=Role.USER, content="hello"))
    await mgr._db_save_messages(
        "sess1",
        [{"role": "user", "content": "hello", "harness_notice": None}],
    )
    await mgr._db_save_plan(
        "sess1",
        PlanState(
            steps=[{"id": "s1", "description": "step 1", "status": "done"}],
            approved=True,
        ),
    )

    # 模拟会话被淘汰出内存 / 后端重启
    del mgr._sessions["sess1"]

    # 从 DB 恢复
    restored = await mgr.get_or_restore("sess1")
    assert restored.id == "sess1"
    assert restored.session.config.model == "test-model"
    assert len(restored.ctx.frame.messages) == 1
    assert restored.ctx.frame.messages[0].role == Role.USER
    assert restored.ctx.frame.messages[0].content == "hello"
    assert restored.ctx.plan.approved is True
    assert restored.ctx.plan.steps[0]["description"] == "step 1"
    # 新消息 seq 不能冲突
    assert restored._msg_seq == 1

    # 继续对话: run 应该基于已有上下文继续, 而不是报 404
    # (restore 用 settings 重建了 OpenAICompatClient; 测试中把它替换成 FakeLLM 验证续跑)
    restored.session.llm = FakeLLM()
    result = await mgr.run("sess1", "next")
    assert result.error is None
    # [hello, next, assistant 继续回答]
    assert len(restored.ctx.frame.messages) == 3
    assert restored.ctx.frame.messages[-1].role == Role.ASSISTANT
    assert restored.ctx.frame.messages[-2].role == Role.USER
    # 新消息已落库
    db_msgs = await mgr._db_load_messages("sess1")
    assert len(db_msgs) == 3
    assert db_msgs[-2]["role"] == "user"
    assert db_msgs[-2]["content"] == "next"
    assert db_msgs[-1]["role"] == "assistant"
