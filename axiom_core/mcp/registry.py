"""Agent Registry (能力目录服务) 接入。

registry 的发现层是一个标准 MCP Server (Streamable HTTP, POST {url}/mcp,
Authorization: Bearer <api_key>), 暴露:
- 工具: search_resources / search_agents / search_mcp_servers (只返回摘要)
- Resources: agent://<name>, mcp://<name>, skill://<name> (resources/read 返回完整信息)

本模块负责把 RegistryConfig 转成 MCPServerConfig 并注入会话的 MCP 连接流程。
注入集中在 Session.prepare() (见 axiom_core.agent.session), 因此 CLI、
POST /api/sessions (请求体透传或 config 兜底)、会话恢复等所有 mcp_servers
生效的路径都自动覆盖。
"""

from __future__ import annotations

import logging

from axiom_core.config import RegistryConfig

from .manager import MCPServerConfig

logger = logging.getLogger(__name__)

# 注入的 MCP server 名; 工具以 mcp__agent-registry__search_resources 形式暴露。
REGISTRY_SERVER_NAME = "agent-registry"


def registry_mcp_config(registry: RegistryConfig) -> MCPServerConfig | None:
    """把 RegistryConfig 转成 MCPServerConfig; 未启用或无 url 返回 None。"""
    if not registry.enabled or not registry.url:
        return None
    headers = {}
    if registry.api_key:
        headers["Authorization"] = f"Bearer {registry.api_key}"
    return MCPServerConfig(
        name=REGISTRY_SERVER_NAME,
        url=registry.url.rstrip("/") + "/mcp",
        headers=headers,
    )


def inject_registry_server(
    mcp_servers: list | None,
    registry: RegistryConfig | None = None,
) -> list:
    """把 registry 作为 MCP server 合并进 mcp_servers 列表 (去重)。

    mcp_servers 各项是 MCPServerConfig。用户显式配置了同名 server 时
    跳过注入 (用户配置优先)。registry 为 None 时从 load_settings() 读;
    读取失败静默跳过 (不影响主流程)。
    """
    servers = list(mcp_servers or [])
    if registry is None:
        try:
            from axiom_core.config import load_settings

            registry = load_settings().registry
        except Exception as e:
            logger.warning("load registry config failed, skip injection: %s", e)
            return servers
    reg_cfg = registry_mcp_config(registry)
    if reg_cfg is None:
        return servers
    existing = {getattr(s, "name", None) for s in servers}
    if REGISTRY_SERVER_NAME in existing:
        return servers
    return [*servers, reg_cfg]
