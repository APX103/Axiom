"""MCP Server 管理器。


管理多个 MCP server,聚合工具列表,路由工具调用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .client import MCPClient, MCPError

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """单个 MCP server 配置。"""

    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


class MCPServerManager:
    """多 MCP server 管理器。

    用法:
        mgr = MCPServerManager()
        await mgr.add_server(
            MCPServerConfig("web_search_prime", url, {"Authorization":"Bearer ..."}))
        tools = mgr.list_all_tools()  # [{server_name, tool_name, description, inputSchema}]
        result = await mgr.call_tool("web_search_prime", "search", {"query":"..."})
    """

    def __init__(self) -> None:
        self._servers: dict[str, MCPClient] = {}
        self._configs: dict[str, MCPServerConfig] = {}

    async def add_server(self, config: MCPServerConfig) -> bool:
        """连接一个 MCP server。成功返回 True。

        连接失败不抛 (不影响其他 server),返回 False。
        会话运行中可动态调用 (单事件循环, dict 操作安全);
        同名 server 已连接时幂等返回 True (避免泄漏旧 client)。
        """
        if not config.enabled:
            return False
        if config.name in self._servers:
            return True
        client = MCPClient(config.url, headers=config.headers)
        try:
            await client.connect()
            self._servers[config.name] = client
            self._configs[config.name] = config
            logger.info("MCP server '%s' connected: %d tools", config.name, len(client.tools))
            return True
        except (MCPError, Exception) as e:
            logger.warning("MCP server '%s' connect failed: %s", config.name, e)
            await client.close()
            return False

    def list_all_tools(self) -> list[dict[str, Any]]:
        """聚合所有 server 的工具。

        返回 [{server_name, name, description, input_schema}]。
        name 是原始工具名 (不带前缀),供代理注册时加前缀。
        """
        tools = []
        for sname, client in self._servers.items():
            for t in client.tools:
                tools.append(
                    {
                        "server_name": sname,
                        "name": t.get("name", "unknown"),
                        "description": t.get("description", ""),
                        "input_schema": t.get("inputSchema", {"type": "object", "properties": {}}),
                    }
                )
        return tools

    def tool_names(self) -> list[str]:
        """所有工具的全名 (带 server 前缀)。"""
        return [f"mcp__{t['server_name']}__{t['name']}" for t in self.list_all_tools()]

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """路由工具调用到对应 server。"""
        client = self._servers.get(server_name)
        if client is None:
            return (
                f"Error: MCP server '{server_name}' not connected. Available: "
                f"{list(self._servers.keys())}"
            )
        try:
            return await client.call_tool(tool_name, arguments)
        except MCPError as e:
            return f"Error calling MCP {server_name}.{tool_name}: {e}"

    async def list_resources(self, server_name: str) -> list[dict[str, Any]]:
        """拉取对应 server 的 resource 列表。server 未连接或失败时返回空列表。"""
        client = self._servers.get(server_name)
        if client is None:
            logger.warning("MCP server '%s' not connected, list_resources skipped", server_name)
            return []
        try:
            return await client.list_resources()
        except MCPError as e:
            logger.warning("MCP %s resources/list failed: %s", server_name, e)
            return []

    async def read_resource(self, server_name: str, uri: str) -> str:
        """路由 resource 读取到对应 server。"""
        client = self._servers.get(server_name)
        if client is None:
            return (
                f"Error: MCP server '{server_name}' not connected. Available: "
                f"{list(self._servers.keys())}"
            )
        try:
            return await client.read_resource(uri)
        except MCPError as e:
            return f"Error reading MCP resource {server_name}.{uri}: {e}"

    def is_connected(self, server_name: str) -> bool:
        return server_name in self._servers

    async def close_all(self) -> None:
        for client in self._servers.values():
            await client.close()
        self._servers.clear()
        self._configs.clear()
