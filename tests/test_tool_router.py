"""工具路由测试。

对照原版 _tool_router.executeToolCalls (0871.js:1496)。
测试: 并发执行、错误捕获、未知工具、超时。
"""

from __future__ import annotations

import asyncio

import pytest

from operon.llm.messages import ToolUseBlock
from operon.tools.registry import ToolRegistry
from operon.tools.router import ToolRouter


@pytest.fixture
def router():
    reg = ToolRegistry()

    async def echo(text: str):
        return f"echo:{text}"

    async def slow():
        await asyncio.sleep(10)
        return "should not reach"

    async def boom():
        raise RuntimeError("intentional error")

    reg.register(
        "echo", "echo tool",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        handler=echo,
    )
    reg.register("slow", "slow tool", handler=slow)
    reg.register("boom", "error tool", handler=boom)
    return ToolRouter(reg, default_timeout=0.5)


@pytest.mark.asyncio
async def test_successful_execution(router):
    """正常工具调用返回结果。"""
    result = await router.execute_one(ToolUseBlock(id="t1", name="echo", input={"text": "hi"}))
    assert result.is_error is False
    assert result.content == "echo:hi"
    assert result.tool_use_id == "t1"


@pytest.mark.asyncio
async def test_unknown_tool(router):
    """未知工具 → is_error=True,内容提示可用工具。"""
    result = await router.execute_one(ToolUseBlock(id="t1", name="nonexistent", input={}))
    assert result.is_error is True
    assert "unknown tool" in result.content.lower()
    assert "echo" in result.content  # 提示可用工具


@pytest.mark.asyncio
async def test_tool_exception_caught(router):
    """工具抛异常 → is_error=True,不崩溃 (对照原版行为)。"""
    result = await router.execute_one(ToolUseBlock(id="t1", name="boom", input={}))
    assert result.is_error is True
    assert "intentional error" in result.content


@pytest.mark.asyncio
async def test_tool_timeout(router):
    """超时 → is_error=True。"""
    result = await router.execute_one(ToolUseBlock(id="t1", name="slow", input={}), timeout=0.2)
    assert result.is_error is True
    assert "timed out" in result.content.lower()


@pytest.mark.asyncio
async def test_concurrent_execution(router):
    """多个工具并发执行,结果对齐。"""
    tool_uses = [
        ToolUseBlock(id="t1", name="echo", input={"text": "a"}),
        ToolUseBlock(id="t2", name="echo", input={"text": "b"}),
        ToolUseBlock(id="t3", name="boom", input={}),
    ]
    results = await router.execute_tool_calls(tool_uses)
    assert len(results) == 3
    assert results[0].content == "echo:a"
    assert results[1].content == "echo:b"
    assert results[2].is_error is True


@pytest.mark.asyncio
async def test_empty_tool_calls(router):
    """空 tool_use 列表返回空。"""
    results = await router.execute_tool_calls([])
    assert results == []
