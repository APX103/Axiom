"""LLM 连通端到端测试 (离线 mock)。

证明 OpenAICompatClient 的完整往返通路正确:
发请求 → 收响应 → 解析 tool_use → 构造 tool_result → 二次请求携带工具结果。

用 httpx 的 MockTransport 拦截,模拟一个国内模型的完整工具调用回合,
不依赖真实 API key。真实模型连通测试见 test_llm_live.py (默认 skip)。
"""

from __future__ import annotations

import json

import httpx
import pytest

from axiom_core.llm.messages import Message, Role, TextBlock, ToolResultBlock, ToolUseBlock
from axiom_core.llm.openai_compat import OpenAICompatClient


def _make_client(handler) -> OpenAICompatClient:
    """构造一个用 MockTransport 拦截请求的 client。"""
    transport = httpx.MockTransport(handler)
    return OpenAICompatClient(
        base_url="https://fake.example.com/v1",
        api_key="sk-fake",
        model="fake-model",
        transport=transport,
    )


@pytest.mark.asyncio
async def test_full_tool_call_roundtrip():
    """完整工具调用往返:

    回合1: 用户提问 → 模型返回 tool_use(get_weather)
    回合2: 提交 tool_result → 模型返回最终文本
    """
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        body = json.loads(request.content)
        last_msg = body["messages"][-1]

        if call_count == 1:
            # 第一次: 用户提问,模型决定调工具
            assert last_msg["role"] == "user"
            assert last_msg["content"] == "北京天气如何?"
            return httpx.Response(
                200,
                json={
                    "model": "fake-model",
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "get_weather",
                                            "arguments": '{"city":"北京"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10},
                },
            )
        else:
            # 第二次: 应携带 tool result
            # OpenAI 格式里 tool result 是 role=tool 消息
            assert any(m.get("role") == "tool" for m in body["messages"]), (
                "第二次请求应包含 tool result 消息"
            )
            tool_msgs = [m for m in body["messages"] if m.get("role") == "tool"]
            assert tool_msgs[0]["content"] == "晴, 22°C"
            assert tool_msgs[0]["tool_call_id"] == "call_1"
            # 还应包含之前的 assistant tool_calls
            asst_msgs = [m for m in body["messages"] if m.get("role") == "assistant"]
            assert asst_msgs and asst_msgs[-1].get("tool_calls")
            return httpx.Response(
                200,
                json={
                    "model": "fake-model",
                    "choices": [
                        {
                            "message": {"content": "北京今天晴,气温 22°C。"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 40, "completion_tokens": 15},
                },
            )

    client = _make_client(handler)
    from axiom_core.llm.messages import ToolDefinition

    tools = [
        ToolDefinition(
            name="get_weather",
            description="Get weather for a city",
            parameters={"type": "object", "properties": {"city": {"type": "string"}}},
        )
    ]

    # 回合 1
    resp1 = await client.chat(
        [Message(role=Role.USER, content="北京天气如何?")],
        system="You are helpful.",
        tools=tools,
    )
    assert resp1.stop_reason.value == "tool_use"
    assert len(resp1.content) == 1
    tu = resp1.content[0]
    assert isinstance(tu, ToolUseBlock)
    assert tu.name == "get_weather"
    assert tu.input == {"city": "北京"}

    # 回合 2: 提交工具结果
    messages = [
        Message(role=Role.USER, content="北京天气如何?"),
        Message(role=Role.ASSISTANT, content=resp1.content),
        Message(
            role=Role.USER,
            content=[ToolResultBlock(tool_use_id="call_1", content="晴, 22°C")],
        ),
    ]
    resp2 = await client.chat(messages, system="You are helpful.")
    assert resp2.stop_reason.value == "end_turn"
    assert isinstance(resp2.content[0], TextBlock)
    assert "22°C" in resp2.content[0].text
    assert call_count == 2
