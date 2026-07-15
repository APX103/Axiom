"""MCP skill 文档生成 + host.mcp 测试。

对照原版 0772.js RxO (skill 生成) + 0814.js:842 (host.mcp)。
"""

from __future__ import annotations

import pytest

from operon.host import make_host
from operon.mcp.manager import MCPServerManager
from operon.mcp.skill_gen import generate_mcp_skills, is_mcp_skill, mcp_skill_name


class FakeManager(MCPServerManager):
    """跳过真实连接的假 manager,直接注入 tools。"""

    def __init__(self, tools_list):
        super().__init__()
        self._fake_tools = tools_list

    def list_all_tools(self):
        return self._fake_tools


def test_mcp_skill_name():
    assert mcp_skill_name("web_search_prime") == "mcp-web-search-prime"
    assert mcp_skill_name("Zinc") == "mcp-zinc"
    long_name = mcp_skill_name("a" * 100)
    assert len(long_name) <= 64  # 截断到 ≤64


def test_is_mcp_skill():
    assert is_mcp_skill("mcp-zinc") is True
    assert is_mcp_skill("paper-narrative") is False


def test_generate_mcp_skills():
    """MCP server 工具生成 mcp-* skill 文档。"""
    tools = [
        {"server_name": "web_search", "name": "search", "description": "[MCP:web_search] search the web", "input_schema": {}},
        {"server_name": "web_search", "name": "fetch", "description": "[MCP:web_search] fetch url", "input_schema": {}},
        {"server_name": "zinc", "name": "query", "description": "search molecules", "input_schema": {}},
    ]
    mgr = FakeManager(tools)
    skills = generate_mcp_skills(mgr)
    assert len(skills) == 2  # 2 个 server = 2 个 mcp-* skill
    names = [s.name for s in skills]
    assert "mcp-web-search" in names
    assert "mcp-zinc" in names
    # 文档含工具索引
    ws = next(s for s in skills if s.name == "mcp-web-search")
    assert "**search**" in ws.body
    assert "**fetch**" in ws.body
    assert "[MCP:" not in ws.body  # 前缀被去掉


def test_generate_mcp_skill_source():
    tools = [{"server_name": "srv", "name": "t", "description": "d", "input_schema": {}}]
    mgr = FakeManager(tools)
    skills = generate_mcp_skills(mgr)
    assert skills[0].source == "mcp"


@pytest.mark.asyncio
async def test_host_mcp_method():
    """host.mcp(server, tool, **kwargs) 显式调用。"""
    # 用 fake manager (不真连)
    tools = [{"server_name": "srv", "name": "hello", "description": "d", "input_schema": {}}]
    mgr = FakeManager(tools)

    # 注入一个 fake call_tool
    async def fake_call(server, tool, args):
        return f"called {server}.{tool} with {args}"

    mgr.call_tool = fake_call

    host = make_host(mcp_manager=mgr)
    result = await host.mcp("srv", "hello", q="test")
    assert "called srv.hello" in result
    assert "q" in result


@pytest.mark.asyncio
async def test_host_mcp_no_manager():
    """无 mcp_manager 时 host.mcp 报错。"""
    host = make_host()
    with pytest.raises(RuntimeError, match="no MCP manager"):
        await host.mcp("srv", "tool")
