"""Phase 2: Registry REST 客户端 + registry_connect_mcp_server 动态挂载测试。

覆盖:
1. RegistryClient 包络解包 (config/credential/agent credential)、错误传播、认证头
2. client_from_settings 工厂 (enabled/url 校验)
3. registry_connect_mcp_server 工具: 成功挂载、凭据合并、凭据不进返回值、
   幂等、动态注册后工具可调、各类友好错误

全部用 httpx.MockTransport / fake client, 无真实网络。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import axiom_core.config
import axiom_core.mcp.manager as manager_mod
import axiom_core.tools.builtins.registry_connect as rc_mod
from axiom_core.config import RegistryConfig
from axiom_core.mcp.manager import MCPServerManager
from axiom_core.registry.client import RegistryClient, RegistryError, client_from_settings
from axiom_core.tools.registry import ToolRegistry

# ---------- fake registry REST server ----------


def _make_registry_transport(
    *,
    config_code: int = 0,
    credential_code: int = 0,
    wrap_mcp_servers: bool = False,
) -> httpx.MockTransport:
    """假 registry REST: 三个端点, 统一 {code,msg,data} 包络。"""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization", "")
        seen["path"] = request.url.path
        path = request.url.path

        def envelope(code: int, data, msg: str = "ok") -> httpx.Response:
            return httpx.Response(200, json={"code": code, "msg": msg, "data": data})

        if path == "/api/v1/mcp-servers/web-search/config":
            if config_code != 0:
                return envelope(config_code, None, "mcp server not found")
            cfg = {"url": "https://mcp.example.com/web-search/mcp", "headers": {"X-Cfg": "1"}}
            data = {"mcpServers": {"web-search": cfg}} if wrap_mcp_servers else cfg
            return envelope(0, data)
        if path == "/api/v1/mcp-servers/web-search/credential":
            if credential_code != 0:
                return envelope(credential_code, None, "no credential")
            return envelope(0, {"http_headers": {"Authorization": "Bearer secret-token"}})
        if path == "/api/v1/agents/writer/credential":
            return envelope(0, {"scheme_type": "bearer", "scheme_config": {}, "credential": "tok"})
        if path == "/api/v1/mcp-servers/broken/config":
            return httpx.Response(500, text="internal error")
        return httpx.Response(404, json={"code": 404, "msg": "not found", "data": None})

    transport = httpx.MockTransport(handler)
    transport.seen = seen  # type: ignore[attr-defined]
    return transport


# ---------- 1. RegistryClient ----------


@pytest.mark.asyncio
async def test_client_get_config_unwraps_envelope():
    transport = _make_registry_transport()
    client = RegistryClient("https://r.example.com/", api_key="ar_xxx", transport=transport)

    config = await client.get_mcp_server_config("web-search")

    assert config["url"] == "https://mcp.example.com/web-search/mcp"
    assert config["headers"] == {"X-Cfg": "1"}
    # 认证头 + base_url 尾部斜杠处理
    assert transport.seen["authorization"] == "Bearer ar_xxx"  # type: ignore[attr-defined]
    assert transport.seen["path"] == "/api/v1/mcp-servers/web-search/config"  # type: ignore[attr-defined]
    await client.close()


@pytest.mark.asyncio
async def test_client_get_config_unwraps_mcp_servers_wrapper():
    """data 包一层 {"mcpServers": {...}} 时也能解出配置片段。"""
    client = RegistryClient(
        "https://r.example.com", transport=_make_registry_transport(wrap_mcp_servers=True)
    )
    config = await client.get_mcp_server_config("web-search")
    assert config["url"] == "https://mcp.example.com/web-search/mcp"
    await client.close()


@pytest.mark.asyncio
async def test_client_credentials():
    client = RegistryClient("https://r.example.com", transport=_make_registry_transport())

    cred = await client.get_mcp_server_credential("web-search")
    assert cred == {"http_headers": {"Authorization": "Bearer secret-token"}}

    agent_cred = await client.get_agent_credential("writer")
    assert agent_cred["scheme_type"] == "bearer"
    assert agent_cred["credential"] == "tok"
    await client.close()


@pytest.mark.asyncio
async def test_client_nonzero_code_raises_with_msg():
    client = RegistryClient(
        "https://r.example.com", transport=_make_registry_transport(config_code=1001)
    )
    with pytest.raises(RegistryError, match="mcp server not found") as exc_info:
        await client.get_mcp_server_config("web-search")
    assert exc_info.value.code == 1001
    await client.close()


@pytest.mark.asyncio
async def test_client_http_error_raises():
    client = RegistryClient("https://r.example.com", transport=_make_registry_transport())
    with pytest.raises(RegistryError, match="HTTP 500"):
        await client.get_mcp_server_config("broken")
    await client.close()


# ---------- 2. client_from_settings ----------


def test_client_from_settings_disabled_raises(monkeypatch):
    monkeypatch.setattr(
        axiom_core.config, "load_settings", lambda: type("S", (), {"registry": RegistryConfig()})()
    )
    with pytest.raises(RegistryError, match="未启用"):
        client_from_settings()


def test_client_from_settings_builds_client(monkeypatch):
    reg = RegistryConfig(enabled=True, url="https://r.example.com", api_key="ar_xxx")
    monkeypatch.setattr(
        axiom_core.config, "load_settings", lambda: type("S", (), {"registry": reg})()
    )
    client = client_from_settings()
    assert client.base_url == "https://r.example.com"
    assert client._headers["Authorization"] == "Bearer ar_xxx"


# ---------- 3. registry_connect_mcp_server 工具 ----------


class FakeMCPClient:
    """替换 manager 里的 MCPClient, 记录 headers, 提供一个 ping 工具。"""

    instances: list[FakeMCPClient] = []

    def __init__(self, url, headers=None):
        self.url = url
        self.headers = headers or {}
        self.tools = [
            {
                "name": "ping",
                "description": "ping tool",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]
        FakeMCPClient.instances.append(self)

    async def connect(self):
        pass

    async def call_tool(self, name, arguments=None):
        return f"pong:{name}"

    async def close(self):
        pass


class FakeRegistryClient:
    """替换工具里的 client_from_settings 返回值。"""

    def __init__(self, config=None, credential=None, config_error=None):
        self._config = config or {"url": "https://mcp.example.com/web-search/mcp"}
        self._credential = (
            credential
            if credential is not None
            else {"http_headers": {"Authorization": "Bearer secret-token"}}
        )
        self._config_error = config_error
        self.closed = False

    async def get_mcp_server_config(self, name):
        if self._config_error:
            raise RegistryError(self._config_error)
        return self._config

    async def get_mcp_server_credential(self, name):
        return self._credential

    async def close(self):
        self.closed = True


def _make_ctx(mcp_manager=None):
    from axiom_core.frames.model import Frame, FrameStatus
    from axiom_core.frames.service import FrameService
    from axiom_core.tools.context import ToolContext

    frame = Frame(
        id="test-frame",
        parent_frame_id=None,
        root_frame_id="test-frame",
        agent_name="MAIN",
        status=FrameStatus.PROCESSING,
    )
    ctx = ToolContext(frame=frame, frame_service=FrameService(), workspace=Path("/tmp"))
    ctx.mcp_manager = mcp_manager
    ctx.registry = ToolRegistry()
    return ctx


def _patch(monkeypatch, fake_registry_client):
    # 用模块对象而非字符串路径: test_host.py 会 pop sys.modules["axiom_core"],
    # 字符串解析在全套件下会因父包属性缺失而失败。
    monkeypatch.setattr(manager_mod, "MCPClient", FakeMCPClient)
    monkeypatch.setattr(rc_mod, "client_from_settings", lambda: fake_registry_client)


@pytest.mark.asyncio
async def test_connect_tool_success_registers_and_hides_credential(monkeypatch):
    """成功挂载: 凭据合并进 headers, 工具注册进 ctx.registry 且可调,
    返回值不含凭据。"""
    from axiom_core.tools.builtins.registry_connect import registry_connect_mcp_server

    FakeMCPClient.instances.clear()
    _patch(monkeypatch, FakeRegistryClient())
    manager = MCPServerManager()
    ctx = _make_ctx(mcp_manager=manager)

    result = await registry_connect_mcp_server(ctx, name="web-search")
    parsed = json.loads(result)

    assert parsed["status"] == "connected"
    assert parsed["server"] == "web-search"
    assert parsed["tools"] == ["mcp__web_search__ping"]
    # 安全边界: 凭据不出现在工具返回值
    assert "secret-token" not in result
    assert "Authorization" not in result

    # 凭据合并进 headers (控制代码内)
    assert FakeMCPClient.instances[0].headers == {
        "Authorization": "Bearer secret-token",
    }
    # 动态注册后工具经 ctx.registry 可调 (下一轮即生效的路径)
    tool = ctx.registry._tools["mcp__web_search__ping"]
    assert await tool.handler() == "pong:ping"


@pytest.mark.asyncio
async def test_connect_tool_idempotent(monkeypatch):
    """重复挂载同名 server: 幂等返回, 不重复连接。"""
    from axiom_core.tools.builtins.registry_connect import registry_connect_mcp_server

    FakeMCPClient.instances.clear()
    _patch(monkeypatch, FakeRegistryClient())
    manager = MCPServerManager()
    ctx = _make_ctx(mcp_manager=manager)

    await registry_connect_mcp_server(ctx, name="web-search")
    result = await registry_connect_mcp_server(ctx, name="web-search")
    parsed = json.loads(result)

    assert parsed["status"] == "already_connected"
    assert parsed["tools"] == ["mcp__web_search__ping"]
    assert len(FakeMCPClient.instances) == 1  # 只连了一次


@pytest.mark.asyncio
async def test_connect_tool_no_mcp_manager():
    from axiom_core.tools.builtins.registry_connect import registry_connect_mcp_server

    ctx = _make_ctx(mcp_manager=None)
    result = await registry_connect_mcp_server(ctx, name="web-search")
    assert result.startswith("Error:")


@pytest.mark.asyncio
async def test_connect_tool_registry_disabled(monkeypatch):
    from axiom_core.registry import client as client_mod
    from axiom_core.tools.builtins import registry_connect

    def _raise():
        raise RegistryError("agent registry 未启用或未配置 url (settings.registry)")

    monkeypatch.setattr(registry_connect, "client_from_settings", _raise)
    monkeypatch.setattr(client_mod, "client_from_settings", _raise)

    ctx = _make_ctx(mcp_manager=MCPServerManager())
    result = await registry_connect.registry_connect_mcp_server(ctx, name="web-search")
    assert "未启用" in result


@pytest.mark.asyncio
async def test_connect_tool_registry_error_propagates(monkeypatch):
    from axiom_core.tools.builtins.registry_connect import registry_connect_mcp_server

    _patch(monkeypatch, FakeRegistryClient(config_error="mcp server not found"))
    ctx = _make_ctx(mcp_manager=MCPServerManager())

    result = await registry_connect_mcp_server(ctx, name="web-search")
    assert "mcp server not found" in result


@pytest.mark.asyncio
async def test_connect_tool_no_url_in_config(monkeypatch):
    from axiom_core.tools.builtins.registry_connect import registry_connect_mcp_server

    _patch(monkeypatch, FakeRegistryClient(config={"transport": "stdio", "command": "npx"}))
    ctx = _make_ctx(mcp_manager=MCPServerManager())

    result = await registry_connect_mcp_server(ctx, name="web-search")
    assert "no usable url" in result


def test_connect_tool_registered_in_register_all():
    from axiom_core.tools.builtins import register_all

    ctx = _make_ctx()
    registry = ToolRegistry()
    register_all(registry, ctx)
    assert "registry_connect_mcp_server" in registry.names()
