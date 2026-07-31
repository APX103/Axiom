"""Agent Registry (能力目录服务) 接入测试。

覆盖:
1. RegistryConfig 配置与 Settings/AppSettings 集成 (含 api_key 脱敏)
2. registry_mcp_config / inject_registry_server 注入与去重
3. MCPClient resources/list + resources/read (MockTransport 假 server, 无真实网络)
4. MCPServerManager resources passthrough
5. mcp_read_resource 工具路由
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from axiom_core.config import RegistryConfig, Settings
from axiom_core.mcp.client import MCPClient
from axiom_core.mcp.manager import MCPServerConfig, MCPServerManager
from axiom_core.mcp.registry import (
    REGISTRY_SERVER_NAME,
    inject_registry_server,
    registry_mcp_config,
)
from axiom_core.settings import (
    AppSettings,
    app_settings_to_config_settings,
    config_settings_to_app_settings,
    mask_app_settings,
    mask_key,
    unmask_app_settings,
)

# ---------- 1. 配置 ----------


def test_registry_config_defaults_disabled():
    """RegistryConfig 默认关闭, url/api_key 为空。"""
    reg = RegistryConfig()
    assert reg.enabled is False
    assert reg.url == ""
    assert reg.api_key == ""


def test_settings_has_registry_section():
    """Settings 挂 registry 节, 可用嵌套 dict 构造。"""
    s = Settings()
    assert s.registry.enabled is False
    s2 = Settings(registry={"enabled": True, "url": "https://r.example.com", "api_key": "ar_x"})
    assert s2.registry.enabled is True
    assert s2.registry.url == "https://r.example.com"


def test_app_settings_registry_roundtrip_and_masking():
    """AppSettings.registry 参与 mask/unmask 与双向转换。"""
    app = AppSettings(
        registry=RegistryConfig(
            enabled=True, url="https://r.example.com", api_key="ar_secret_token_1234"
        )
    )
    # mask: api_key 脱敏
    masked = mask_app_settings(app)
    assert masked.registry.api_key == mask_key("ar_secret_token_1234")
    assert masked.registry.url == "https://r.example.com"
    # unmask: 提交回 mask 值时保留真实 key; 提交新值时用新值
    merged = unmask_app_settings(masked, app)
    assert merged.registry.api_key == "ar_secret_token_1234"
    changed = masked.model_copy(
        update={"registry": masked.registry.model_copy(update={"api_key": "ar_new_token_5678"})}
    )
    merged2 = unmask_app_settings(changed, app)
    assert merged2.registry.api_key == "ar_new_token_5678"
    # AppSettings → Settings 转换透传 registry
    cfg = app_settings_to_config_settings(app)
    assert cfg.registry.enabled is True
    assert cfg.registry.api_key == "ar_secret_token_1234"
    # Settings → AppSettings 转换透传 registry
    back = config_settings_to_app_settings(cfg)
    assert back.registry.url == "https://r.example.com"


# ---------- 2. 注入 ----------


def test_registry_mcp_config_disabled_returns_none():
    assert registry_mcp_config(RegistryConfig()) is None
    assert registry_mcp_config(RegistryConfig(enabled=True, url="")) is None


def test_registry_mcp_config_builds_server():
    """enabled + url → MCPServerConfig, url 加 /mcp 后缀, 带 Bearer 头。"""
    cfg = registry_mcp_config(
        RegistryConfig(enabled=True, url="https://r.example.com/", api_key="ar_xxx")
    )
    assert cfg is not None
    assert cfg.name == REGISTRY_SERVER_NAME == "agent-registry"
    assert cfg.url == "https://r.example.com/mcp"
    assert cfg.headers == {"Authorization": "Bearer ar_xxx"}


def test_registry_mcp_config_without_api_key():
    cfg = registry_mcp_config(RegistryConfig(enabled=True, url="https://r.example.com"))
    assert cfg is not None
    assert cfg.headers == {}


def test_inject_registry_server_appends_and_dedupes():
    """注入到列表末尾; 用户已配同名 server 时跳过 (用户配置优先)。"""
    reg = RegistryConfig(enabled=True, url="https://r.example.com", api_key="ar_xxx")
    user_srv = MCPServerConfig(name="web_search_prime", url="https://x/mcp")

    merged = inject_registry_server([user_srv], reg)
    assert [s.name for s in merged] == ["web_search_prime", "agent-registry"]

    # 同名用户配置 → 不重复注入
    dup = MCPServerConfig(name="agent-registry", url="https://mine/mcp")
    merged2 = inject_registry_server([dup], reg)
    assert len(merged2) == 1
    assert merged2[0].url == "https://mine/mcp"

    # None 列表也能注入 (registry 单独生效)
    merged3 = inject_registry_server(None, reg)
    assert [s.name for s in merged3] == ["agent-registry"]

    # registry 未启用 → 原样返回
    assert inject_registry_server([user_srv], RegistryConfig()) == [user_srv]


# ---------- 3. MCPClient resources (假 server) ----------


def _make_fake_mcp_transport() -> httpx.MockTransport:
    """假 MCP server: 支持 initialize / tools/list / resources/list / resources/read。"""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload.get("method")
        rid = payload.get("id")
        if method == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
                },
                headers={"Mcp-Session-Id": "sess-1"},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": rid, "result": {"tools": []}},
            )
        if method == "resources/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "resources": [
                            {"uri": "agent://writer", "name": "writer"},
                            {"uri": "skill://survey", "name": "survey"},
                        ]
                    },
                },
            )
        if method == "resources/read":
            uri = payload["params"]["uri"]
            if uri == "agent://writer":
                # SSE 形式响应, 验证 SSE 解析路径
                body = json.dumps({
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "contents": [{
                            "uri": uri,
                            "mimeType": "application/json",
                            "text": '{"endpoint":"https://a2a.example.com/writer","skills":["write"]}',
                        }]
                    },
                })
                return httpx.Response(
                    200, text=f"event: message\ndata: {body}\n\n",
                    headers={"Content-Type": "text/event-stream"},
                )
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32602, "message": f"unknown resource: {uri}"},
                },
            )
        return httpx.Response(400, json={"error": {"message": f"bad method {method}"}})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_client_list_and_read_resources():
    """list_resources 返回 resource 列表; read_resource 提取文本 (含 SSE 响应)。"""
    client = MCPClient("https://fake/mcp", transport=_make_fake_mcp_transport())
    await client.connect()

    resources = await client.list_resources()
    assert len(resources) == 2
    assert resources[0]["uri"] == "agent://writer"

    text = await client.read_resource("agent://writer")
    data = json.loads(text)
    assert data["endpoint"] == "https://a2a.example.com/writer"

    await client.close()


@pytest.mark.asyncio
async def test_client_read_resource_error_raises_mcp_error():
    from axiom_core.mcp.client import MCPError

    client = MCPClient("https://fake/mcp", transport=_make_fake_mcp_transport())
    with pytest.raises(MCPError, match="unknown resource"):
        await client.read_resource("agent://nonexistent")
    await client.close()


# ---------- 4. Manager passthrough ----------


@pytest.mark.asyncio
async def test_manager_resources_passthrough():
    """manager.list_resources / read_resource 路由到对应 server。"""
    mgr = MCPServerManager()
    client = MCPClient("https://fake/mcp", transport=_make_fake_mcp_transport())
    await client.connect()
    mgr._servers["agent-registry"] = client  # 直接注入假 client

    resources = await mgr.list_resources("agent-registry")
    assert len(resources) == 2

    text = await mgr.read_resource("agent-registry", "agent://writer")
    assert "writer" in text

    # 未连接 server 的容错
    assert await mgr.list_resources("nope") == []
    err = await mgr.read_resource("nope", "agent://x")
    assert err.startswith("Error: MCP server 'nope' not connected")

    await mgr.close_all()


# ---------- 5. mcp_read_resource 工具 ----------


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
    return ctx


@pytest.mark.asyncio
async def test_mcp_read_resource_tool_routes():
    """mcp_read_resource 工具路由到 ctx.mcp_manager.read_resource。"""
    from axiom_core.tools.builtins.mcp_read_resource import mcp_read_resource

    mgr = MCPServerManager()
    client = MCPClient("https://fake/mcp", transport=_make_fake_mcp_transport())
    await client.connect()
    mgr._servers["agent-registry"] = client

    ctx = _make_ctx(mcp_manager=mgr)
    text = await mcp_read_resource(ctx, server="agent-registry", uri="agent://writer")
    assert "https://a2a.example.com/writer" in text

    await mgr.close_all()


@pytest.mark.asyncio
async def test_mcp_read_resource_tool_no_manager():
    """未连接 MCP 时返回友好错误, 不抛异常。"""
    from axiom_core.tools.builtins.mcp_read_resource import mcp_read_resource

    ctx = _make_ctx(mcp_manager=None)
    result = await mcp_read_resource(ctx, server="agent-registry", uri="agent://x")
    assert result.startswith("Error:")


def test_mcp_read_resource_registered_in_register_all():
    """register_all 注册 mcp_read_resource 工具。"""
    from axiom_core.tools.builtins import register_all
    from axiom_core.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_all(registry, _make_ctx())
    assert "mcp_read_resource" in registry.names()
