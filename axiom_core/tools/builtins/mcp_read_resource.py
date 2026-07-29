"""mcp_read_resource 工具: 读取已连接 MCP server 的 resource。

通用工具, 不限 Agent Registry。典型用法 (registry 发现层):
1. 用 mcp__agent-registry__search_resources 搜索 (只返回摘要)
2. 用本工具 read agent://<name> / mcp://<name> / skill://<name> 拿完整信息
   (A2A agent 的 endpoint/skills/auth, skill 的完整内容等)
"""

from __future__ import annotations

from axiom_core.tools.context import ToolContext

MCP_READ_RESOURCE_SPEC = {
    "name": "mcp_read_resource",
    "description": (
        "读取一个已连接 MCP server 提供的 resource 完整内容 (MCP resources/read)。"
        "当某个 MCP 工具 (如 search_resources) 只返回摘要并给出 resource URI 时, "
        "用此工具读取详情。server 是 MCP server 名 (如 agent-registry), "
        "uri 是 resource URI (如 agent://<name>, mcp://<name>, skill://<name>)。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "MCP server 名, 如 agent-registry",
            },
            "uri": {
                "type": "string",
                "description": "resource URI, 如 agent://<name> 或 skill://<name>",
            },
        },
        "required": ["server", "uri"],
    },
}


async def mcp_read_resource(ctx: ToolContext, server: str, uri: str, **_kw) -> str:
    """读取 MCP resource, 返回文本内容。"""
    manager = ctx.mcp_manager
    if manager is None:
        return "Error: no MCP servers connected in this session"
    return await manager.read_resource(server, uri)
