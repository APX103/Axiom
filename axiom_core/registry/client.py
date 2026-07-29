"""Agent Registry (能力目录服务) REST 客户端。

registry 除 MCP 发现端点 (见 axiom_core.mcp.registry) 外, 还有一组 REST
(base 为 registry.url, 认证 Authorization: Bearer <api_key>):

- GET /api/v1/mcp-servers/:name/config     → data 是标准 mcpServers 配置片段
- GET /api/v1/mcp-servers/:name/credential → data 形如 {http_headers: {...}} (明文凭据)
- GET /api/v1/agents/:name/credential      → data 形如 {scheme_type, scheme_config, credential}

统一响应包络: {"code":0,"msg":"ok","data":{...}}; 非 0 code / HTTP 错误抛 RegistryError。

安全边界: 明文凭据只能在控制代码里用 (注入 HTTP header),
绝不能出现在工具返回值 / LLM context 里。
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx


class RegistryError(Exception):
    """Registry REST 调用错误。"""

    def __init__(self, message: str, *, code: int | None = None):
        super().__init__(message)
        self.code = code


class RegistryClient:
    """Registry REST 客户端 (httpx async, 轻量风格同 axiom_core.mcp.client)。

    用法:
        client = RegistryClient("https://registry.example.com", api_key="ar_xxx")
        config = await client.get_mcp_server_config("web-search")
        credential = await client.get_mcp_server_credential("web-search")
        await client.close()
    """

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._headers = {"Accept": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, transport=self._transport)
        return self._client

    async def _get(self, path: str) -> Any:
        """GET 并解包 {code,msg,data} 包络, 返回 data 字段。"""
        client = await self._ensure_client()
        resp = await client.get(self.base_url + path, headers=self._headers)
        if resp.status_code >= 400:
            raise RegistryError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            body = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise RegistryError(f"非 JSON 响应: {resp.text[:300]}") from e
        code = body.get("code")
        if code != 0:
            raise RegistryError(body.get("msg") or f"registry error code {code}", code=code)
        return body.get("data")

    async def get_mcp_server_config(self, name: str) -> dict[str, Any]:
        """取 MCP server 的标准 mcpServers 配置片段 (含 url/transport 等)。"""
        data = await self._get(f"/api/v1/mcp-servers/{quote(name, safe='')}/config")
        # 兼容两种形态: 直接 {url,...} 或包一层 {"mcpServers": {name: {...}}}
        if isinstance(data, dict) and "mcpServers" in data:
            servers = data["mcpServers"] or {}
            data = servers.get(name) or next(iter(servers.values()), {})
        return data if isinstance(data, dict) else {}

    async def get_mcp_server_credential(self, name: str) -> dict[str, Any]:
        """取 MCP server 明文凭据, 形如 {http_headers: {...}}。

        安全边界: 返回值含明文, 只允许注入 HTTP header, 不得进工具返回值。
        """
        data = await self._get(f"/api/v1/mcp-servers/{quote(name, safe='')}/credential")
        return data if isinstance(data, dict) else {}

    async def get_agent_credential(self, name: str) -> dict[str, Any]:
        """取 A2A agent 凭据, 形如 {scheme_type, scheme_config, credential}。

        同样含明文, 安全边界同上。
        """
        data = await self._get(f"/api/v1/agents/{quote(name, safe='')}/credential")
        return data if isinstance(data, dict) else {}

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def client_from_settings() -> RegistryClient:
    """从 load_settings().registry 构造 client; 未启用/无 url 抛 RegistryError。"""
    from axiom_core.config import load_settings

    registry = load_settings().registry
    if not registry.enabled or not registry.url:
        raise RegistryError("agent registry 未启用或未配置 url (settings.registry)")
    return RegistryClient(registry.url, registry.api_key)
