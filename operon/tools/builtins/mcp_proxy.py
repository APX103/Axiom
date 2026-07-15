"""MCP 工具代理: 把 MCP server 的工具自动注册成 agent 直接可调的工具。


本项目: 自动注册成 agent 工具,工具名 mcp__<server>__<tool>,体验更好。

session 创建时:
1. manager.list_all_tools() 拿到所有 MCP 工具
2. 对每个工具调 register_mcp_tools(registry, manager) 注册
3. agent 就能直接调用 mcp__web_search_prime__search(...)
"""

from __future__ import annotations

import re

from operon.mcp.manager import MCPServerManager
from operon.tools.registry import ToolRegistry


def _sanitize(name: str) -> str:
    """工具名只保留字母数字下划线。"""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def tool_name(server: str, tool: str) -> str:
    """MCP 工具的全名: mcp__<server>__<tool>。"""
    return f"mcp__{_sanitize(server)}__{_sanitize(tool)}"


def register_mcp_tools(registry: ToolRegistry, manager: MCPServerManager) -> int:
    """把所有已连接 MCP server 的工具注册到 registry。

    返回注册的工具数。
    工具 description 标注来源 server。parameters 透传 MCP inputSchema。
    handler 路由到 manager.call_tool。
    """
    count = 0
    for t in manager.list_all_tools():
        server_name = t["server_name"]
        tool_name_raw = t["name"]
        full_name = tool_name(server_name, tool_name_raw)
        description = t["description"] or f"MCP tool {tool_name_raw}"
        description = f"[MCP:{server_name}] {description}"
        parameters = t["input_schema"] or {"type": "object", "properties": {}}

        # 闭包捕获正确的 server/tool 名 (避免循环变量陷阱)
        def _make_handler(srv: str, tool: str):
            async def _handler(**kwargs):
                return await manager.call_tool(srv, tool, kwargs)

            return _handler

        try:
            registry.register(
                name=full_name,
                description=description,
                parameters=parameters,
                handler=_make_handler(server_name, tool_name_raw),
            )
            count += 1
        except Exception:
            # 重复名或 schema 问题,跳过
            pass
    return count
