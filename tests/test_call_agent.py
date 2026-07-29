"""Phase 3: call_agent 工具测试。

覆盖:
1. completed 路径: 经 agent:// resource 拿 endpoint, A2A 调用, 凭据只进 header 不进返回值
2. 三种认证 header 构造: none / apiKey (scheme_config header 名) / http bearer
3. 不支持的认证类型 (oauth2 等) → 明确错误
4. input_required: 返回 context_id/task_id + ask_user 续接提示; 续接参数透传
5. 错误路径: registry 未启用 / 无 MCP manager / 超时 / failed 状态
6. register_all 注册

全部用 fake + httpx.MockTransport, 无真实网络 (写法同 tests/test_registry_client.py)。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import axiom_core.tools.builtins.call_agent as ca_mod
from axiom_core.a2a.client import A2AClient
from axiom_core.registry.client import RegistryError
from axiom_core.tools.registry import ToolRegistry

AGENT_NAME = "writer"
AGENT_ENDPOINT = "https://agent.example.com/a2a"
AGENT_INFO = json.dumps(
    {
        "endpoint": AGENT_ENDPOINT,
        "skills": [{"id": "write", "description": "写作"}],
        "auth": {"scheme_type": "apiKey"},
        "credential_endpoint": "/api/v1/agents/writer/credential",
    }
)


# ---------- fakes ----------


class FakeMCPManager:
    """只提供 agent-registry 的 read_resource。"""

    def __init__(self, resource_text: str = AGENT_INFO, connected: bool = True):
        self._text = resource_text
        self._connected = connected
        self.read_uris: list[str] = []

    def is_connected(self, name: str) -> bool:
        return self._connected and name == "agent-registry"

    async def read_resource(self, server: str, uri: str) -> str:
        assert server == "agent-registry"
        self.read_uris.append(uri)
        return self._text


class FakeRegistryClient:
    """替换 client_from_settings 返回值 (同 test_registry_client.py 的写法)。"""

    def __init__(self, credential: dict | None = None, error: str | None = None):
        self._credential = credential if credential is not None else {"scheme_type": "none"}
        self._error = error
        self.closed = False

    async def get_agent_credential(self, name: str):
        if self._error:
            raise RegistryError(self._error)
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


def _patch(monkeypatch, a2a_transport, credential=None, registry_error=None):
    """替换 registry client 工厂 + A2AClient (注入 MockTransport, 记录构造参数)。"""
    monkeypatch.setattr(
        ca_mod,
        "client_from_settings",
        lambda: FakeRegistryClient(credential=credential, error=registry_error),
    )
    captured: dict = {}

    def _make_a2a(endpoint, *, headers=None, timeout=120.0, transport=None):
        captured["endpoint"] = endpoint
        captured["headers"] = headers or {}
        captured["timeout"] = timeout
        return A2AClient(endpoint, headers=headers, timeout=timeout, transport=a2a_transport)

    monkeypatch.setattr(ca_mod, "A2AClient", _make_a2a)
    return captured


def _completed_transport(seen: dict | None = None) -> httpx.MockTransport:
    """返回 completed SSE 流的 A2A endpoint; seen 记录请求 headers/body。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["headers"] = dict(request.headers)
            seen["body"] = json.loads(request.content)
        body = (
            "data: "
            + json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "kind": "task",
                        "id": "t-1",
                        "contextId": "ctx-1",
                        "status": {
                            "state": "completed",
                            "message": {
                                "messageId": "m-1",
                                "role": "agent",
                                "parts": [{"kind": "text", "text": "写好了"}],
                            },
                        },
                    },
                }
            )
            + "\n\n"
        )
        return httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream"}
        )

    return httpx.MockTransport(handler)


def _input_required_transport() -> httpx.MockTransport:
    body = (
        "data: "
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "kind": "status-update",
                    "taskId": "t-9",
                    "contextId": "ctx-9",
                    "status": {
                        "state": "input-required",
                        "message": {
                            "messageId": "m-1",
                            "role": "agent",
                            "parts": [{"kind": "text", "text": "想要什么主题?"}],
                        },
                    },
                    "final": True,
                },
            }
        )
        + "\n\n"
    )
    return httpx.MockTransport(
        lambda req: httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream"}
        )
    )


# ---------- 1. completed 路径 ----------


@pytest.mark.asyncio
async def test_call_agent_completed(monkeypatch):
    """凭据只进 HTTP header, 返回值不含凭据。"""
    seen: dict = {}
    captured = _patch(
        monkeypatch,
        _completed_transport(seen),
        credential={"scheme_type": "apiKey", "scheme_config": {"header_name": "X-API-Key"},
                    "credential": "secret-key"},
    )
    ctx = _make_ctx(mcp_manager=FakeMCPManager())

    result = await ca_mod.call_agent(ctx, name=AGENT_NAME, task="写个摘要")
    parsed = json.loads(result)

    assert parsed["status"] == "completed"
    assert parsed["text"] == "写好了"
    assert parsed["context_id"] == "ctx-1"
    # 读了 agent://<name> resource 拿 endpoint
    assert ctx.mcp_manager.read_uris == [f"agent://{AGENT_NAME}"]
    # 凭据进了 HTTP header (控制代码内)
    assert seen["headers"]["x-api-key"] == "secret-key"
    assert captured["endpoint"] == AGENT_ENDPOINT
    assert captured["headers"] == {"X-API-Key": "secret-key"}
    # 安全边界: 凭据绝不出现在工具返回值
    assert "secret-key" not in result
    assert "X-API-Key" not in result


# ---------- 2. 三种认证 header 构造 ----------


def test_auth_headers_none():
    assert ca_mod._build_auth_headers({"scheme_type": "none"}) == {}
    assert ca_mod._build_auth_headers({}) == {}


def test_auth_headers_apikey():
    cred = {
        "scheme_type": "apiKey",
        "scheme_config": {"header_name": "X-Custom-Key"},
        "credential": "k-1",
    }
    assert ca_mod._build_auth_headers(cred) == {"X-Custom-Key": "k-1"}
    # scheme_config 用 "name" 键 (A2A APIKeySecurityScheme 风格)
    cred2 = {"scheme_type": "apiKey", "scheme_config": {"name": "X-API-Key"},
             "credential": "k-2"}
    assert ca_mod._build_auth_headers(cred2) == {"X-API-Key": "k-2"}
    # scheme_config 缺 header 名时默认 X-API-Key
    cred3 = {"scheme_type": "apiKey", "scheme_config": {}, "credential": "k-3"}
    assert ca_mod._build_auth_headers(cred3) == {"X-API-Key": "k-3"}


def test_auth_headers_http_bearer():
    cred = {"scheme_type": "http", "scheme_config": {"scheme": "bearer"}, "credential": "tok"}
    assert ca_mod._build_auth_headers(cred) == {"Authorization": "Bearer tok"}
    # registry 也可能直接给 scheme_type="bearer"
    cred2 = {"scheme_type": "bearer", "scheme_config": {}, "credential": "tok2"}
    assert ca_mod._build_auth_headers(cred2) == {"Authorization": "Bearer tok2"}


def test_auth_headers_unsupported_scheme():
    for scheme in ("oauth2", "openIdConnect", "mutualTLS"):
        cred = {"scheme_type": scheme, "scheme_config": {}, "credential": "x"}
        with pytest.raises(ValueError, match="暂不支持"):
            ca_mod._build_auth_headers(cred)


@pytest.mark.asyncio
async def test_call_agent_no_auth_sends_no_credential_header(monkeypatch):
    seen: dict = {}
    _patch(monkeypatch, _completed_transport(seen), credential={"scheme_type": "none"})
    ctx = _make_ctx(mcp_manager=FakeMCPManager())

    await ca_mod.call_agent(ctx, name=AGENT_NAME, task="任务")

    assert "authorization" not in seen["headers"]
    assert "x-api-key" not in seen["headers"]


@pytest.mark.asyncio
async def test_call_agent_unsupported_scheme_returns_error(monkeypatch):
    _patch(
        monkeypatch,
        _completed_transport(),
        credential={"scheme_type": "oauth2", "scheme_config": {}, "credential": "x"},
    )
    ctx = _make_ctx(mcp_manager=FakeMCPManager())

    result = await ca_mod.call_agent(ctx, name=AGENT_NAME, task="任务")
    assert result.startswith("Error:")
    assert "暂不支持" in result


# ---------- 3. input_required + 续接 ----------


@pytest.mark.asyncio
async def test_call_agent_input_required_and_resume(monkeypatch):
    """input_required 返回 context_id/task_id + ask_user 提示;
    续接调用把 context_id/task_id 透传进 A2A params。"""
    _patch(monkeypatch, _input_required_transport())
    ctx = _make_ctx(mcp_manager=FakeMCPManager())

    result = await ca_mod.call_agent(ctx, name=AGENT_NAME, task="写首诗")
    parsed = json.loads(result)

    assert parsed["status"] == "input_required"
    assert parsed["context_id"] == "ctx-9"
    assert parsed["task_id"] == "t-9"
    assert "想要什么主题?" in parsed["text"]
    assert "ask_user" in parsed["text"]  # 提示 LLM 走 ask_user 续接
    assert parsed["task_id"] not in ("", None)

    # 续接: 同 context_id/task_id 再调用
    seen2: dict = {}
    _patch(monkeypatch, _completed_transport(seen2))
    result2 = await ca_mod.call_agent(
        ctx, name=AGENT_NAME, task="春天", context_id="ctx-9", task_id="t-9"
    )
    parsed2 = json.loads(result2)
    assert parsed2["status"] == "completed"
    message = seen2["body"]["params"]["message"]
    assert message["contextId"] == "ctx-9"
    assert message["taskId"] == "t-9"
    assert message["parts"] == [{"kind": "text", "text": "春天"}]


# ---------- 4. 错误路径 ----------


@pytest.mark.asyncio
async def test_call_agent_no_mcp_manager():
    ctx = _make_ctx(mcp_manager=None)
    result = await ca_mod.call_agent(ctx, name=AGENT_NAME, task="任务")
    assert result.startswith("Error:")
    assert "MCP" in result


@pytest.mark.asyncio
async def test_call_agent_registry_not_connected():
    ctx = _make_ctx(mcp_manager=FakeMCPManager(connected=False))
    result = await ca_mod.call_agent(ctx, name=AGENT_NAME, task="任务")
    assert result.startswith("Error:")
    assert "registry" in result


@pytest.mark.asyncio
async def test_call_agent_registry_disabled(monkeypatch):
    def _raise():
        raise RegistryError("agent registry 未启用或未配置 url (settings.registry)")

    monkeypatch.setattr(ca_mod, "client_from_settings", _raise)
    ctx = _make_ctx(mcp_manager=FakeMCPManager())

    result = await ca_mod.call_agent(ctx, name=AGENT_NAME, task="任务")
    assert "未启用" in result


@pytest.mark.asyncio
async def test_call_agent_credential_error(monkeypatch):
    _patch(monkeypatch, _completed_transport(), registry_error="agent not found")
    ctx = _make_ctx(mcp_manager=FakeMCPManager())

    result = await ca_mod.call_agent(ctx, name=AGENT_NAME, task="任务")
    assert "agent not found" in result


@pytest.mark.asyncio
async def test_call_agent_resource_error_propagates():
    ctx = _make_ctx(mcp_manager=FakeMCPManager(resource_text="Error: MCP server boom"))
    result = await ca_mod.call_agent(ctx, name=AGENT_NAME, task="任务")
    assert result.startswith("Error:")


@pytest.mark.asyncio
async def test_call_agent_resource_not_json():
    ctx = _make_ctx(mcp_manager=FakeMCPManager(resource_text="not json"))
    result = await ca_mod.call_agent(ctx, name=AGENT_NAME, task="任务")
    assert "不是合法 JSON" in result


@pytest.mark.asyncio
async def test_call_agent_no_endpoint_in_resource():
    ctx = _make_ctx(mcp_manager=FakeMCPManager(resource_text=json.dumps({"skills": []})))
    result = await ca_mod.call_agent(ctx, name=AGENT_NAME, task="任务")
    assert "endpoint" in result


@pytest.mark.asyncio
async def test_call_agent_timeout(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    _patch(monkeypatch, httpx.MockTransport(handler))
    ctx = _make_ctx(mcp_manager=FakeMCPManager())

    result = await ca_mod.call_agent(ctx, name=AGENT_NAME, task="任务")
    assert result.startswith("Error:")
    assert "超时" in result


@pytest.mark.asyncio
async def test_call_agent_failed_state(monkeypatch):
    body = (
        "data: "
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "kind": "status-update",
                    "taskId": "t-1",
                    "contextId": "ctx-1",
                    "status": {
                        "state": "failed",
                        "message": {
                            "messageId": "m-1",
                            "role": "agent",
                            "parts": [{"kind": "text", "text": "依赖服务不可用"}],
                        },
                    },
                    "final": True,
                },
            }
        )
        + "\n\n"
    )
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream"}
        )
    )
    _patch(monkeypatch, transport)
    ctx = _make_ctx(mcp_manager=FakeMCPManager())

    result = await ca_mod.call_agent(ctx, name=AGENT_NAME, task="任务")
    assert result.startswith("Error:")
    assert "failed" in result
    assert "依赖服务不可用" in result


# ---------- 5. 注册 ----------


def test_call_agent_registered_in_register_all():
    from axiom_core.tools.builtins import register_all

    ctx = _make_ctx()
    registry = ToolRegistry()
    register_all(registry, ctx)
    assert "call_agent" in registry.names()
