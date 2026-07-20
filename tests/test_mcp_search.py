"""MCP Search 阈值切换 + 元工具测试。

覆盖:
1. 阈值切换: 工具数 ≤ threshold 全量暴露; > threshold 切换元工具
2. mcp_search BM25 检索准确性
3. mcp_call 路由正确性 (含错误名容错)
4. 元工具不破坏既有内置工具

不连真实 MCP server, 用 MockMCPServerManager 构造假工具。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from operon.tools.builtins.mcp_proxy import (
    register_mcp_search_tools,
    register_mcp_tools,
    tool_name,
)
from operon.tools.registry import ToolRegistry


class MockMCPServerManager:
    """模拟 MCPServerManager, 不连真实 server。

    工具列表用构造函数注入, call_tool 记录调用。
    """

    def __init__(self, tools: list[dict[str, Any]]):
        """tools: [{server_name, name, description, input_schema}]"""
        self._tools = tools
        self.calls: list[tuple[str, str, dict]] = []

    def list_all_tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((server_name, tool_name, arguments))
        return f"called {server_name}.{tool_name} with {json.dumps(arguments)}"

    async def close_all(self) -> None:
        pass


def _make_tool(server: str, name: str, desc: str = "") -> dict[str, Any]:
    """构造一个假 MCP 工具 dict。"""
    return {
        "server_name": server,
        "name": name,
        "description": desc,
        "input_schema": {"type": "object", "properties": {}},
    }


# ---------- 1. 阈值切换 ----------


def test_register_below_threshold_uses_full_mode():
    """工具数 ≤ threshold 时, 走全量模式: 注册 mcp__server__tool 形式的直接工具。"""
    tools = [
        _make_tool("search_srv", "web_search", "search the web"),
        _make_tool("search_srv", "fetch_url", "fetch a URL"),
    ]
    manager = MockMCPServerManager(tools)
    registry = ToolRegistry()

    n = register_mcp_tools(registry, manager)

    assert n == 2
    names = registry.names()
    assert "mcp__search_srv__web_search" in names
    assert "mcp__search_srv__fetch_url" in names
    # 不应有 mcp_search / mcp_call 元工具
    assert "mcp_search" not in names
    assert "mcp_call" not in names


def test_register_above_threshold_uses_meta_tools():
    """工具数 > threshold 时, 走元工具模式: 只注册 mcp_search + mcp_call。"""
    tools = [_make_tool("srv", f"tool_{i}", f"tool number {i}") for i in range(50)]
    manager = MockMCPServerManager(tools)
    registry = ToolRegistry()

    n = register_mcp_search_tools(registry, manager)

    assert n == 2
    names = registry.names()
    assert "mcp_search" in names
    assert "mcp_call" in names
    # 不应有任何 mcp__server__tool 直接工具
    direct = [nm for nm in names if nm.startswith("mcp__")]
    assert len(direct) == 0


# ---------- 2. mcp_search 检索 ----------


@pytest.mark.asyncio
async def test_mcp_search_finds_relevant_tool():
    """mcp_search 应该按相关性返回工具, web 相关查询命中 web_search。"""
    tools = [
        _make_tool("web_srv", "web_search", "search the web for information"),
        _make_tool("web_srv", "fetch_url", "fetch content of a URL"),
        _make_tool("papers_srv", "search_papers", "search academic papers"),
        _make_tool("math_srv", "calculate", "do math calculation"),
        _make_tool("file_srv", "read_file", "read a file from disk"),
    ]
    manager = MockMCPServerManager(tools)
    registry = ToolRegistry()
    register_mcp_search_tools(registry, manager)

    # 找 mcp_search 工具的 handler
    search_tool = registry._tools["mcp_search"]  # type: ignore[attr-defined]
    result_json = await search_tool.handler(query="search the web", max_results=3)
    result = json.loads(result_json)

    assert "tools" in result
    assert result["total_indexed"] == 5
    # web_search 应该排在结果里
    names = [t["name"] for t in result["tools"]]
    assert "mcp__web_srv__web_search" in names
    # 不相关的 calculate / read_file 不应出现在前 3
    # (BM25 可能不完美, 但最相关的应在前面)
    assert result["tools"][0]["name"] == "mcp__web_srv__web_search"


@pytest.mark.asyncio
async def test_mcp_search_empty_query_returns_empty():
    """空 query 或无 token 的 query 返回空结果, 不抛异常。"""
    tools = [_make_tool("srv", "tool", "desc")]
    manager = MockMCPServerManager(tools)
    registry = ToolRegistry()
    register_mcp_search_tools(registry, manager)

    search_tool = registry._tools["mcp_search"]  # type: ignore[attr-defined]
    result_json = await search_tool.handler(query="")
    result = json.loads(result_json)
    assert result["tools"] == []


@pytest.mark.asyncio
async def test_mcp_search_no_tools_at_all():
    """无 MCP 工具时不抛异常, 返回友好错误。"""
    manager = MockMCPServerManager([])
    registry = ToolRegistry()
    register_mcp_search_tools(registry, manager)

    search_tool = registry._tools["mcp_search"]  # type: ignore[attr-defined]
    result_json = await search_tool.handler(query="anything")
    result = json.loads(result_json)
    assert "error" in result


# ---------- 3. mcp_call 路由 ----------


@pytest.mark.asyncio
async def test_mcp_call_routes_to_correct_server_and_tool():
    """mcp_call 根据 mcp__<server>__<tool> 名正确路由到 manager.call_tool。"""
    tools = [_make_tool("web_srv", "web_search", "search")]
    manager = MockMCPServerManager(tools)
    registry = ToolRegistry()
    register_mcp_search_tools(registry, manager)

    call_tool = registry._tools["mcp_call"]  # type: ignore[attr-defined]
    result = await call_tool.handler(
        name="mcp__web_srv__web_search",
        arguments={"query": "hello"},
    )

    assert "called web_srv.web_search" in result
    assert len(manager.calls) == 1
    assert manager.calls[0] == ("web_srv", "web_search", {"query": "hello"})


@pytest.mark.asyncio
async def test_mcp_call_unknown_name_returns_helpful_error():
    """未知工具名时返回错误 + 提示 + 示例列表, 不抛异常。"""
    tools = [
        _make_tool("srv", "tool_a", "desc a"),
        _make_tool("srv", "tool_b", "desc b"),
    ]
    manager = MockMCPServerManager(tools)
    registry = ToolRegistry()
    register_mcp_search_tools(registry, manager)

    call_tool = registry._tools["mcp_call"]  # type: ignore[attr-defined]
    result = await call_tool.handler(name="mcp__srv__nonexistent", arguments={})
    parsed = json.loads(result)

    assert "error" in parsed
    assert "nonexistent" in parsed["error"]
    assert "hint" in parsed
    assert len(manager.calls) == 0  # 没被调用


@pytest.mark.asyncio
async def test_mcp_call_no_arguments_default_empty():
    """mcp_call 不传 arguments 时, 默认传空 dict。"""
    tools = [_make_tool("srv", "ping", "ping tool")]
    manager = MockMCPServerManager(tools)
    registry = ToolRegistry()
    register_mcp_search_tools(registry, manager)

    call_tool = registry._tools["mcp_call"]  # type: ignore[attr-defined]
    await call_tool.handler(name="mcp__srv__ping")
    assert manager.calls[0][2] == {}


# ---------- 4. 元工具和内置工具共存 ----------


def test_meta_tools_do_not_conflict_with_builtins():
    """mcp_search / mcp_call 注册后, 内置工具仍能正常注册。"""
    from operon.tools.builtins import register_all
    from operon.tools.context import ToolContext
    from operon.frames.model import Frame, FrameStatus
    from operon.frames.service import FrameService
    from pathlib import Path

    tools = [_make_tool("srv", f"t{i}", "desc") for i in range(50)]
    manager = MockMCPServerManager(tools)
    registry = ToolRegistry()

    # 先注册 MCP 元工具
    register_mcp_search_tools(registry, manager)
    # 再注册内置工具
    frame_service = FrameService()
    frame = Frame(
        id="test-frame",
        parent_frame_id=None,
        root_frame_id="test-frame",
        agent_name="MAIN",
        status=FrameStatus.PROCESSING,
    )
    ctx = ToolContext(
        frame=frame,
        frame_service=frame_service,
        workspace=Path("/tmp"),
    )
    register_all(registry, ctx)

    names = registry.names()
    # 元工具 + 内置工具都应在
    assert "mcp_search" in names
    assert "mcp_call" in names
    assert "write_file" in names
    assert "read_file" in names
    assert "bash" in names


# ---------- 5. tool_name 函数 ----------


def test_tool_name_sanitization():
    """tool_name 把特殊字符替换为下划线。"""
    assert tool_name("my-server", "web/search") == "mcp__my_server__web_search"
    assert tool_name("srv", "tool") == "mcp__srv__tool"
