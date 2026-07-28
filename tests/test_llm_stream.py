"""LLM 流式 chat 单测 (阶段: SSE + 真·打字机)。

覆盖:
- StreamAggregator: text 增量聚合 + tool_call 增量聚合 + finalize
- OpenAICompatClient.chat_stream: SSE 响应解析 (mock httpx stream)
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from axiom_core.llm.message_adapter import StreamAggregator
from axiom_core.llm.messages import StopReason, ToolUseBlock

# ---------- StreamAggregator ----------


def test_stream_aggregator_text_delta():
    """text 增量聚合 + finalize。"""
    agg = StreamAggregator(model="m")
    chunks = [
        {"model": "m", "choices": [{"delta": {"role": "assistant", "content": "Hello"}}]},
        {"choices": [{"delta": {"content": " world"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    deltas = [agg.feed(c) for c in chunks]
    assert deltas == [("Hello", ""), (" world", ""), ("", "")]
    resp = agg.finalize()
    assert resp.model == "m"
    assert resp.content[0].text == "Hello world"
    assert resp.stop_reason == StopReason.END_TURN


def test_stream_aggregator_tool_call_delta():
    """tool_call arguments 增量 JSON 字符串聚合。"""
    agg = StreamAggregator()
    # tool_call 分多个 chunk 到达: name 和 arguments 都是增量
    chunks = [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "write"}},
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"path":"a.'}},
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "tex"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    for c in chunks:
        agg.feed(c)
    resp = agg.finalize()
    assert resp.stop_reason == StopReason.TOOL_USE
    assert len(resp.content) == 1
    tu = resp.content[0]
    assert isinstance(tu, ToolUseBlock)
    assert tu.id == "call_1"
    assert tu.name == "write"
    assert tu.input == {"path": "a.tex"}


def test_stream_aggregator_mixed_text_and_tool():
    """text + tool_call 混合输出。"""
    agg = StreamAggregator()
    chunks = [
        {"choices": [{"delta": {"content": "Let me write that."}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "f", "arguments": "{}"}},
        ]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    deltas = [agg.feed(c) for c in chunks]
    assert deltas[0] == ("Let me write that.", "")
    resp = agg.finalize()
    assert len(resp.content) == 2  # text + tool_use
    assert resp.content[0].text == "Let me write that."
    assert isinstance(resp.content[1], ToolUseBlock)


def test_stream_aggregator_usage():
    """usage 在末尾 chunk (部分 provider 在 stream_options include_usage 时给)。"""
    agg = StreamAggregator()
    agg.feed({"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]})
    agg.feed({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}})
    resp = agg.finalize()
    assert resp.usage.input_tokens == 10
    assert resp.usage.output_tokens == 2


# ---------- chat_stream (mock httpx) ----------


class _FakeSSEStream:
    """模拟 httpx 的 async stream context manager (aiter_lines)。"""

    def __init__(self, sse_lines: list[str]):
        self._lines = sse_lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


@pytest.mark.asyncio
async def test_chat_stream_parses_sse(monkeypatch):
    """chat_stream 解析 OpenAI SSE 格式, yield text 增量 + final。"""
    from axiom_core.llm.messages import Message, Role
    from axiom_core.llm.openai_compat import OpenAICompatClient

    def sse(data: dict[str, Any]) -> str:
        return f"data: {json.dumps(data)}"

    sse_lines = [
        sse({"choices": [{"delta": {"content": "Hi "}}]}),
        sse({"choices": [{"delta": {"content": "there"}}]}),
        sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]

    client = OpenAICompatClient(base_url="http://fake", api_key="k", model="m")

    # mock _client.stream 返回 fake SSE
    def fake_stream(*args, **kwargs):
        return _FakeSSEStream(sse_lines)

    monkeypatch.setattr(client._client, "stream", fake_stream)

    events = []
    async for ev in client.chat_stream([Message(role=Role.USER, content="hi")]):
        events.append(ev)

    # 应该有 2 个 text delta + 1 个 final
    text_events = [e for e in events if e["type"] == "text"]
    final_events = [e for e in events if e["type"] == "final"]
    assert len(text_events) == 2
    assert text_events[0]["delta"] == "Hi "
    assert text_events[1]["delta"] == "there"
    assert len(final_events) == 1
    resp = final_events[0]["response"]
    assert resp.content[0].text == "Hi there"
    await client.close()
