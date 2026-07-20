"""共享 pytest fixture 和辅助工具。

注意: 这里只定义新的测试用得上的 fixture, 不强制既有测试迁移, 避免破坏现有
250 个测试 (8 份重复的 FakeLLM 保持不动)。

新测试 (test_trace.py / test_mcp_search.py 等) 用这里的 fixture。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operon.llm.base import LLMClient
from operon.llm.messages import (
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    TokenUsage,
    ToolUseBlock,
)


class FakeLLM(LLMClient):
    """按脚本返回响应的假 LLM。responses 是一个 list, 每次 chat 弹一个。

    与 tests/test_agent_loop.py 的 FakeLLM 行为一致, 抽到 conftest 供新测试复用。
    """

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls = 0
        self.received_messages: list[list[Message]] = []

    async def chat(
        self,
        messages,
        *,
        system=None,
        tools=None,
        model=None,
        max_tokens=8192,
        temperature=None,
        **kw,
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
    """临时 workspace 目录。"""
    return tmp_path


@pytest.fixture
def fake_llm_factory():
    """返回 FakeLLM 类, 测试自己构造实例。"""
    return FakeLLM


@pytest.fixture
def text_resp():
    return _text_resp


@pytest.fixture
def tool_resp():
    return _tool_resp


@pytest.fixture
def mixed_resp():
    return _mixed_resp
