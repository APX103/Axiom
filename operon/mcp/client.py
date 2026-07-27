"""MCP Client — streamable HTTP JSON-RPC。


divergences: 不引入官方 MCP SDK,手写轻量 client (httpx + SSE 解析),更可控。

MCP streamable HTTP 协议:
- POST JSON-RPC 请求到 server URL
- Accept: application/json, text/event-stream
- 响应可能是普通 JSON (Content-Type: application/json) 或 SSE 流
- SSE: 多个 data: <json> 行,取最后一条 result 或 notification
- 认证: Authorization: Bearer <key> (或自定义 header)

生命周期: initialize → (initialized notification) → tools/list → tools/call
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class MCPError(Exception):
    """MCP 调用错误。"""

    def __init__(self, message: str, *, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class MCPClient:
    """单个 MCP server 的 streamable HTTP 客户端。

    用法:
        client = MCPClient(url, headers={"Authorization":"Bearer ..."})
        await client.connect()       # initialize + tools/list
        tools = client.tools         # 缓存的工具列表
        result = await client.call_tool("search", {"query":"..."})
        await client.close()
    """

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 60.0,
        name: str = "axiom-core",
        version: str = "0.0.1",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.url = url
        self.name = name
        self.version = version
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(headers or {}),
        }
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._req_id = 0
        self._initialized = False
        self._session_id: str | None = None  # streamable HTTP 的会话 id (Mcp-Session-Id)
        self.tools: list[dict[str, Any]] = []

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, transport=self._transport)
        return self._client

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """发一个 JSON-RPC 请求,返回 result 字段。

        处理两种响应: 普通 JSON / SSE 流。
        streamable HTTP 有状态: initialize 后续请求带 Mcp-Session-Id header。
        """
        client = await self._ensure_client()
        payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        if params is not None:
            payload["params"] = params

        # 注入 session id (initialize 之后必需)
        headers = dict(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        resp = await client.post(self.url, json=payload, headers=headers)
        # 非 2xx 但可能有 JSON 错误体
        if resp.status_code >= 400:
            # 尝试解析错误
            try:
                body = resp.json()
                if "error" in body:
                    err = body["error"]
                    raise MCPError(err.get("message", "MCP error"), code=err.get("code"))
            except (json.JSONDecodeError, ValueError):
                pass
            raise MCPError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        return self._parse_response(resp)

    def _parse_response(self, resp: httpx.Response) -> dict[str, Any]:
        """解析响应: SSE 流或普通 JSON。

        SSE: 多个 event,data: <json> 行。取带 id 匹配的 result。
        实际中很多 MCP server 对单次请求返回单个 SSE event 含完整 result。
        """
        ctype = resp.headers.get("content-type", "")
        text = resp.text

        if "text/event-stream" in ctype:
            # 解析 SSE: 聚合所有 data: 行
            result: dict[str, Any] | None = None
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    # 跳过 notification (无 id),取 result
                    if "result" in data or "error" in data:
                        result = data
            if result is None:
                raise MCPError("SSE 流中无 result")
            data = result
        else:
            try:
                data = resp.json()
            except json.JSONDecodeError as e:
                raise MCPError(f"非 JSON 响应: {text[:300]}") from e

        if "error" in data and data["error"] is not None:
            err = data["error"]
            raise MCPError(
                err.get("message", "MCP error"), code=err.get("code"), data=err.get("data")
            )
        return data.get("result", {})

    async def connect(self) -> None:
        """初始化握手 + 拉取工具列表。

        streamable HTTP 有状态: initialize 响应头返回 Mcp-Session-Id,
        后续请求必须带它 (否则部分 server 返回 401)。
        """
        if self._initialized:
            return
        client = await self._ensure_client()
        # initialize — 单独发,抓 session id 响应头
        init_payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": self.name, "version": self.version},
            },
        }
        resp = await client.post(self.url, json=init_payload, headers=self._headers)
        if resp.status_code < 400:
            # 抓 Mcp-Session-Id (大小写不敏感)
            for k, v in resp.headers.items():
                if k.lower() == "mcp-session-id":
                    self._session_id = v
                    break
            self._parse_response(resp)  # 校验无 error
        # notifications/initialized (无 id,无需 result)
        await self._notify("notifications/initialized", {})
        # tools/list
        result = await self._request("tools/list", {})
        self.tools = result.get("tools", []) if isinstance(result, dict) else []
        self._initialized = True

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        """发 notification (无 id,不期望 result)。"""
        client = await self._ensure_client()
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            await client.post(self.url, json=payload, headers=self._headers)
        except httpx.HTTPError:
            pass  # notification 失败不影响主流程

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """调用工具。返回 result.content (通常 MCP 工具返回 {content:[{type,text}]})."""
        if not self._initialized:
            await self.connect()
        result = await self._request("tools/call", {"name": name, "arguments": arguments or {}})
        return _extract_tool_content(result)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _extract_tool_content(result: Any) -> str:
    """从 MCP tools/call 结果提取文本。

    MCP 工具返回格式: {content: [{type:"text", text:"..."}, ...], isError?: bool}
    转成纯文本字符串 (给 agent 看)。
    """
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        if result.get("isError"):
            parts = [c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)]
            return "Error: " + "\n".join(parts)
        content = result.get("content")
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict):
                    if c.get("type") == "text":
                        parts.append(c.get("text", ""))
                    elif c.get("type") == "image":
                        parts.append("[image omitted]")
                    else:
                        parts.append(str(c))
                else:
                    parts.append(str(c))
            return "\n".join(parts)
        if "text" in result:
            return result["text"]
    return json.dumps(result, ensure_ascii=False, default=str)
