"""registry_connect_mcp_server 工具: 把 registry 里发现的 MCP server 动态挂载进会话。

典型流程 (配合 Agent Registry 发现层):
1. mcp__agent-registry__search_mcp_servers / search_resources 搜索 (摘要)
2. mcp_read_resource 读 mcp://<name> 详情
3. 用本工具挂载: 取 config 片段 + 凭据 → 合并 headers → 动态连接 →
   把该 server 的工具注册进会话 (runner 每轮重新取 registry.definitions(),
   下一轮即可直接调用 mcp__<name>__<tool>)

安全边界: 明文凭据只用于注入 HTTP header, 工具返回值只含状态/server 名/
工具名列表, 绝不含凭据或敏感 header。
"""

from __future__ import annotations

import json
from typing import Any

from axiom_core.mcp.manager import MCPServerConfig
from axiom_core.registry.client import RegistryError, client_from_settings
from axiom_core.tools.context import ToolContext

from .mcp_proxy import register_mcp_tools, tool_name

REGISTRY_CONNECT_MCP_SERVER_SPEC = {
    "name": "registry_connect_mcp_server",
    "description": (
        "把 Agent Registry 里发现的 MCP server 动态挂载到当前会话: 自动获取它的 "
        "连接配置和凭据, 连上之后它的工具会以 mcp__<name>__<tool> 形式可直接调用。"
        "先用 agent-registry 的 search 工具找到 server 名, 再调用本工具。"
        "重复挂载同名 server 是幂等的。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "registry 里的 MCP server 名 (与 search/resource 结果一致)",
            },
        },
        "required": ["name"],
    },
}


def _server_tool_names(manager: Any, server: str) -> list[str]:
    """该 server 已注册工具的全名列表 (mcp__<server>__<tool>)。"""
    return [
        tool_name(t["server_name"], t["name"])
        for t in manager.list_all_tools()
        if t["server_name"] == server
    ]


async def registry_connect_mcp_server(ctx: ToolContext, name: str, **_kw) -> str:
    """动态挂载 registry 中的 MCP server。返回 JSON 字符串 (不含凭据)。"""
    manager = ctx.mcp_manager
    if manager is None:
        return "Error: no MCP manager in this session (MCP 未连接)"

    # 幂等: 已连接同名 server 直接返回现状
    if manager.is_connected(name):
        return json.dumps(
            {
                "status": "already_connected",
                "server": name,
                "tools": _server_tool_names(manager, name),
            },
            ensure_ascii=False,
        )

    try:
        client = client_from_settings()
    except RegistryError as e:
        return f"Error: {e}"

    try:
        config = await client.get_mcp_server_config(name)
        try:
            credential = await client.get_mcp_server_credential(name)
        except RegistryError:
            credential = {}  # 无凭据的公开 server: 忽略, 继续不带凭据连接
    except RegistryError as e:
        return f"Error: registry: {e}"
    finally:
        await client.close()

    url = config.get("url") or ""
    if not url:
        return (
            f"Error: registry returned no usable url for MCP server '{name}'"
            " (仅支持 HTTP MCP server)"
        )

    # 凭据合并进 headers (只在控制代码内使用, 不外泄)
    headers = dict(config.get("headers") or {})
    headers.update((credential or {}).get("http_headers") or {})

    ok = await manager.add_server(MCPServerConfig(name=name, url=url, headers=headers))
    if not ok:
        return f"Error: failed to connect MCP server '{name}'"

    # 把该 server 的工具注册进会话 registry (已注册的重复名会被跳过),
    # runner 每轮重新取 definitions(), 下一轮即生效。
    if ctx.registry is not None:
        register_mcp_tools(ctx.registry, manager)

    return json.dumps(
        {"status": "connected", "server": name, "tools": _server_tool_names(manager, name)},
        ensure_ascii=False,
    )
