"""阶段 0 验收测试。

覆盖:
1. 包 import 链通畅
2. 消息格式转换 (内部 ↔ OpenAI) 正确
3. DB schema 能建表 (四张核心表)
4. Rolling Compact 常数对照原版 (照搬正确性)
5. token 估算与原版一致 (d8=4)

不含真实 LLM 调用 (那需要 API key,放在 test_llm_live.py,默认 skip)。
"""

from __future__ import annotations

import json

import pytest

from axiom_core import (
    CHARS_PER_TOKEN,
    Message,
    OpenAICompatClient,
    Role,
    Settings,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
    estimate_tokens,
)
from axiom_core.llm.message_adapter import messages_to_openai, response_from_openai, tools_to_openai
from axiom_core.llm.token_counter import TokenCounter

# ---------- 1. import 链 ----------


def test_imports():
    """公开 API 全部可 import。"""
    assert CHARS_PER_TOKEN == 4  # 对照原版 0836.js:417 d8=4
    assert OpenAICompatClient is not None
    assert Settings is not None


# ---------- 2. 消息格式转换 ----------


def test_tools_to_openai():
    """ToolDefinition → OpenAI tools 格式。"""
    tools = [
        ToolDefinition(
            name="get_weather",
            description="Get weather",
            parameters={"type": "object", "properties": {"city": {"type": "string"}}},
        )
    ]
    out = tools_to_openai(tools)
    assert out == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    ]


def test_messages_user_text_to_openai():
    """user 纯文本 → OpenAI user 消息。"""
    msgs = [Message(role=Role.USER, content="hello")]
    out = messages_to_openai(msgs, system="You are helpful")
    assert out[0] == {"role": "system", "content": "You are helpful"}
    assert out[1] == {"role": "user", "content": "hello"}


def test_messages_assistant_tool_use_to_openai():
    """assistant tool_use block → OpenAI tool_calls 顶层字段。"""
    msgs = [
        Message(
            role=Role.ASSISTANT,
            content=[
                TextBlock(text="Let me check"),
                ToolUseBlock(id="call_1", name="search", input={"q": "transformer"}),
            ],
        )
    ]
    out = messages_to_openai(msgs, system=None)
    assert out[0]["role"] == "assistant"
    assert out[0]["content"] == "Let me check"
    assert out[0]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "search", "arguments": json.dumps({"q": "transformer"})},
        }
    ]


def test_messages_tool_result_to_openai():
    """user 内嵌 tool_result → 独立的 OpenAI role=tool 消息。

    这是 Anthropic 风格 (tool_result 塞 user) → OpenAI 风格 (独立 tool 消息) 的关键转换。
    """
    msgs = [
        Message(
            role=Role.USER,
            content=[
                ToolResultBlock(tool_use_id="call_1", content="sunny, 22C"),
            ],
        )
    ]
    out = messages_to_openai(msgs, system=None)
    assert out == [
        {"role": "tool", "tool_call_id": "call_1", "content": "sunny, 22C"}
    ]


def test_response_from_openai_text():
    """OpenAI 纯文本响应 → 内部 LLMResponse。"""
    raw = {
        "model": "deepseek-chat",
        "choices": [
            {"message": {"content": "Hello!"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }
    resp = response_from_openai(raw)
    assert len(resp.content) == 1
    assert resp.content[0].text == "Hello!"
    assert resp.stop_reason.value == "end_turn"
    assert resp.usage.input_tokens == 10
    assert resp.usage.output_tokens == 2


def test_response_from_openai_tool_calls():
    """OpenAI tool_calls 响应 → 内部 ToolUseBlock。

    验收核心: 能从国内模型的响应里正确解析出 tool_use。
    """
    raw = {
        "model": "deepseek-chat",
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city": "Beijing"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20},
    }
    resp = response_from_openai(raw)
    assert resp.stop_reason.value == "tool_use"
    assert len(resp.content) == 1
    tu = resp.content[0]
    assert isinstance(tu, ToolUseBlock)
    assert tu.id == "call_abc"
    assert tu.name == "get_weather"
    assert tu.input == {"city": "Beijing"}


def test_response_from_openai_bad_arguments():
    """模型输出非法 JSON arguments → 降级保留原始字符串,不崩溃。"""
    raw = {
        "model": "x",
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "f", "arguments": "not json{"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    resp = response_from_openai(raw)
    assert resp.content[0].input == {"_raw": "not json{"}


# ---------- 3. DB schema ----------


@pytest.mark.asyncio
async def test_db_schema_creates_tables(tmp_path):
    """四张核心表能建表。对照原版 0110.js。"""
    from axiom_core.db.schema import (
        Artifact,
        ArtifactVersion,
        Frame,
        Project,
        VerificationCheck,
    )
    from axiom_core.db.session import init_engine, session_factory

    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = await init_engine(db_url)
    Session = session_factory(engine)

    async with Session() as session:
        from sqlalchemy import select

        # Project
        proj = Project(id="proj_1", name="test")
        session.add(proj)
        await session.flush()

        # Frame (根 frame: parent=None, root=self)
        frame = Frame(
            id="frame_root",
            project_id="proj_1",
            agent_name="MAIN",
            status="processing",
            root_frame_id="frame_root",  # 根 frame 的 root = self
        )
        session.add(frame)
        await session.flush()

        # Artifact + Version (互相引用: art.latest_version_id→ver.id,
        # ver.artifact_id→art.id,需先各自 flush 再回填 latest,避免循环 FK 检查)
        art = Artifact(
            id="art_1",
            project_id="proj_1",
            root_frame_id="frame_root",
            filename="paper.md",
        )
        session.add(art)
        await session.flush()

        ver = ArtifactVersion(
            id="ver_1",
            artifact_id="art_1",
            version_number=1,
            content_type="text/markdown",
            size_bytes=100,
            checksum="abc123",
            storage_path="/tmp/paper.md",
        )
        session.add(ver)
        await session.flush()
        # ver 已存在,现在才能回填 latest_version_id
        art.latest_version_id = "ver_1"
        await session.flush()

        # VerificationCheck
        check = VerificationCheck(
            id="vc_1",
            root_frame_id="frame_root",
            claim="The sky is blue",
            verdict="pass",
            source_ref="observation",
        )
        session.add(check)
        await session.commit()

        # 读回验证
        frames = (await session.execute(select(Frame))).scalars().all()
        assert len(frames) == 1
        assert frames[0].agent_name == "MAIN"

        vcs = (await session.execute(select(VerificationCheck))).scalars().all()
        assert len(vcs) == 1
        assert vcs[0].verdict == "pass"

    await engine.dispose()


# ---------- 4. Rolling Compact 常数对照 ----------


def test_rolling_compact_constants_match_original():
    """常数严格对照原版 0848.js (见 docs/mapping.md 常数表)。

    任何常数改动都应在 docs/divergences.md 记录理由。
    """
    from axiom_core.config import RollingCompactConfig

    cfg = RollingCompactConfig()
    # 逐条对照原版 (mapping.md 常数表)
    assert cfg.chars_per_token == 4  # d8 (0836.js:417)
    assert cfg.output_ceiling == 32000  # aSz (0848.js:2356)
    assert cfg.min_chunk_tokens == 4096  # Xx_ (0848.js:2346)
    assert cfg.max_fork_failures == 3  # po (0848.js:2357)
    assert cfg.l2_prefix_budget_ratio == 0.4  # lSz (0848.js:2345)
    assert cfg.kb_ratio == 0.7  # cSz (0848.js:2343)
    assert cfg.ka_floor == 50000  # mSz (0848.js:2342)
    assert cfg.degenerate_draft_ratio == 0.25  # dSz (0848.js:2349)
    assert cfg.context_ceiling == 500000  # 0039.js:307
    assert cfg.ka_ratio == 0.2  # 0039.js:306


# ---------- 5. token 估算 ----------


def test_estimate_tokens_matches_original():
    """字符估算与原版一致: len(text)//4。

    原版 (0848.js:347): Math.floor(G / d8), d8=4
    """
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 4) == 1
    assert estimate_tokens("a" * 8) == 2
    assert estimate_tokens("a" * 9) == 2  # floor(9/4)=2


def test_token_counter_fallback_to_chars():
    """无 tiktoken 时降级到字符估算。"""
    counter = TokenCounter(use_tiktoken=False)
    assert counter.count("a" * 40) == 10  # 40/4
