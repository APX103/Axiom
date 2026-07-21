"""Agent 循环测试。

用 FakeLLM (按脚本返回响应) 精确控制循环,验证状态机每个分支。
对照原版 0871.js 的 _processLlmResponse / _handleNaturalCompletion。

测试覆盖:
1. 无工具正常退出 (natural)
2. 有工具 → 多轮 → 退出
3. max_tokens 继续
4. 空内容 end_turn 重试
5. 达到 max_iterations
6. 工具错误不崩溃
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operon.agent.runner import RunResultKind
from operon.agent.session import Session, SessionConfig
from operon.llm.base import LLMClient
from operon.llm.messages import (
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)


class FakeLLM(LLMClient):
    """按脚本返回响应的假 LLM。responses 是一个 list,每次 chat 弹一个。"""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls = 0
        self.received_messages: list[list[Message]] = []

    async def chat(
        self, messages, *, system=None, tools=None, model=None, max_tokens=8192,
        temperature=None, **kw
    ):
        self.calls += 1
        self.received_messages.append([m.model_copy() for m in messages])
        if not self.responses:
            raise AssertionError("FakeLLM ran out of scripted responses (infinite loop?)")
        return self.responses.pop(0)

    def count_tokens(self, text):
        return len(text) // 4

    async def close(self):
        pass


def _text_resp(text: str, model: str = "fake") -> LLMResponse:
    return LLMResponse(
        content=[TextBlock(text=text)],
        stop_reason=StopReason.END_TURN,
        model=model,
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


def _tool_resp(name: str, tool_id: str = "t1", **inputs) -> LLMResponse:
    return LLMResponse(
        content=[ToolUseBlock(id=tool_id, name=name, input=inputs)],
        stop_reason=StopReason.TOOL_USE,
        model="fake",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


def _mixed_resp(text: str, name: str, tool_id: str = "t1", **inputs) -> LLMResponse:
    return LLMResponse(
        content=[TextBlock(text=text), ToolUseBlock(id=tool_id, name=name, input=inputs)],
        stop_reason=StopReason.TOOL_USE,
        model="fake",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


# ---------- 1. 无工具正常退出 ----------


@pytest.mark.asyncio
async def test_natural_exit_no_tools(workspace):
    """模型直接回复文本,无工具调用 → natural 退出。"""
    llm = FakeLLM([_text_resp("你好!我是助手。")])
    session = Session(llm=llm, config=SessionConfig(workspace=workspace))
    result = await session.run("你好")

    assert result.kind == RunResultKind.NATURAL
    assert result.iterations == 1
    assert "助手" in result.final_text
    assert llm.calls == 1


# ---------- 2. 有工具 → 多轮 → 退出 ----------


@pytest.mark.asyncio
async def test_tool_call_then_exit(workspace):
    """模型先调工具,拿到结果后再回复 → 2 轮后退出。"""
    llm = FakeLLM(
        [
            _mixed_resp("我来算一下", "python", code="print(2+2)"),
            _text_resp("2+2 = 4"),
        ]
    )
    session = Session(llm=llm, config=SessionConfig(workspace=workspace))
    result = await session.run("算 2+2")

    assert result.kind == RunResultKind.NATURAL
    assert result.iterations == 2
    assert llm.calls == 2
    # 第二次调用应包含 tool_result
    assert any(
        isinstance(b, ToolResultBlock)
        for m in llm.received_messages[1]
        for b in (m.content if isinstance(m.content, list) else [])
    )


# ---------- 3. max_tokens 继续 ----------


@pytest.mark.asyncio
async def test_max_tokens_continues(workspace):
    """max_tokens 不应终止循环,应继续到正常退出。"""
    llm = FakeLLM(
        [
            LLMResponse(
                content=[TextBlock(text="部分回")],
                stop_reason=StopReason.MAX_TOKENS,
                model="fake",
                usage=TokenUsage(input_tokens=10, output_tokens=5),
            ),
            _text_resp("完整回复"),
        ]
    )
    session = Session(llm=llm, config=SessionConfig(workspace=workspace))
    result = await session.run("继续")

    assert result.kind == RunResultKind.NATURAL
    assert llm.calls == 2


# ---------- 4. 空内容 end_turn 重试 ----------


@pytest.mark.asyncio
async def test_empty_end_turn_retries(workspace):
    """空内容 end_turn 应触发重试提示 (对照 0871.js:1344)。"""
    llm = FakeLLM(
        [
            LLMResponse(content=[], stop_reason=StopReason.END_TURN, model="fake"),
            _text_resp("这次有内容了"),
        ]
    )
    session = Session(llm=llm, config=SessionConfig(workspace=workspace))
    result = await session.run("说话")

    assert result.kind == RunResultKind.NATURAL
    assert result.final_text == "这次有内容了"


# ---------- 5. 达到 max_iterations ----------


@pytest.mark.asyncio
async def test_max_iterations(workspace):
    """持续调用工具 → 达到 max_iterations 退出。"""
    # 每轮都调 python,永不退出
    responses = [_mixed_resp(f"第{i}轮", "python", code="1") for i in range(10)]
    llm = FakeLLM(responses)
    session = Session(llm=llm, config=SessionConfig(workspace=workspace, max_iterations=3))
    result = await session.run("循环跑")

    assert result.kind == RunResultKind.MAX_ITERS
    assert result.iterations == 3


# ---------- 6. 工具错误不崩溃 ----------


@pytest.mark.asyncio
async def test_tool_error_doesnt_crash(workspace):
    """工具执行错误应回填 is_error=True,循环继续 (对照原版行为)。"""
    llm = FakeLLM(
        [
            _mixed_resp("调一个会错的工具", "bash", command="exit 1"),
            _text_resp("工具失败了,我换个方法。"),
        ]
    )
    session = Session(llm=llm, config=SessionConfig(workspace=workspace))
    result = await session.run("测试错误处理")

    assert result.kind == RunResultKind.NATURAL
    assert llm.calls == 2  # 错误后继续了第二轮


# ---------- 7. 未知工具 ----------


@pytest.mark.asyncio
async def test_unknown_tool(workspace):
    """调用未注册工具 → 错误回填,循环继续。"""
    llm = FakeLLM(
        [
            _mixed_resp("调不存在的工具", "nonexistent_tool", foo="bar"),
            _text_resp("那个工具不存在,我直接回答。"),
        ]
    )
    session = Session(llm=llm, config=SessionConfig(workspace=workspace))
    result = await session.run("测试")

    assert result.kind == RunResultKind.NATURAL


# ---------- 8. token 用量累计 ----------


@pytest.mark.asyncio
async def test_usage_accumulates(workspace):
    """token 用量应跨轮累计到 frame。"""
    llm = FakeLLM(
        [_mixed_resp("算", "python", code="1"), _text_resp("done")]
    )
    session = Session(llm=llm, config=SessionConfig(workspace=workspace))
    result = await session.run("算")

    assert result.usage["input_tokens"] == 20  # 2 轮 × 10
    assert result.usage["output_tokens"] == 10  # 2 轮 × 5
