"""A2A 协议客户端 — JSON-RPC 2.0 over HTTP POST + SSE 流式。

divergences: 不引入官方 a2a-sdk, 手写轻量 client (httpx + SSE 解析),
风格对齐 axiom_core.mcp.client。

协议要点 (pin A2A v1.0.1):
- POST JSON-RPC 请求到 agent endpoint, 流式方法 message/stream (SSE),
  非流式回退 message/send (server 不支持流式时)。
- params: {"message": {"messageId": <uuid>, "role": "user",
  "parts": [{"kind": "text", "text": ...}], "contextId"?, "taskId"?}}
- 流帧四种: task / message / statusUpdate / artifactUpdate;
  终结态 completed | failed | canceled | input-required。
- 多轮: 同一 contextId 串联; input-required 时同 taskId + contextId 续接。

用法:
    result = await send_message_streaming(
        "https://agent.example.com/a2a", "帮我写一段代码",
        headers={"Authorization": "Bearer ..."},
    )
    if result.state == "input-required":
        # 用 result.context_id / result.task_id 续接
        ...
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

# A2A 终结态
TERMINAL_STATES = ("completed", "failed", "canceled", "input-required")


class A2AError(Exception):
    """A2A 调用错误 (JSON-RPC error / HTTP 错误 / 协议异常)。"""

    def __init__(self, message: str, *, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass
class A2AResult:
    """一次 message/stream (或 message/send 回退) 聚合后的结果。

    state: 终结态 (completed/failed/canceled/input-required) 或中间态。
    text: 拼接的 agent 回复文本 (message parts + artifact 文本)。
    error: failed/canceled 时与 text 相同 (便于调用方区分), 否则 None。
    """

    state: str
    text: str = ""
    context_id: str | None = None
    task_id: str | None = None
    error: str | None = None


class _Accumulator:
    """流帧聚合器: 收集文本、跟踪最新 state / contextId / taskId。"""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.state: str | None = None
        self.context_id: str | None = None
        self.task_id: str | None = None

    def add_message(self, message: Any) -> None:
        text = _message_text(message)
        if text:
            self.texts.append(text)

    def add_status(
        self, status: Any, *, task_id: str | None = None, context_id: str | None = None
    ) -> None:
        if task_id:
            self.task_id = task_id
        if context_id:
            self.context_id = context_id
        if not isinstance(status, dict):
            return
        state = status.get("state")
        if state:
            self.state = state
        self.add_message(status.get("message"))

    def add_artifact(
        self, artifact: Any, *, task_id: str | None = None, context_id: str | None = None
    ) -> None:
        if task_id:
            self.task_id = task_id
        if context_id:
            self.context_id = context_id
        text = _parts_text((artifact or {}).get("parts") if isinstance(artifact, dict) else None)
        if text:
            self.texts.append(text)

    def result(self) -> A2AResult:
        text = "\n".join(self.texts)
        error = text if self.state in ("failed", "canceled") else None
        return A2AResult(
            state=self.state or "unknown",
            text=text,
            context_id=self.context_id,
            task_id=self.task_id,
            error=error,
        )


def _parts_text(parts: Any) -> str:
    """从 A2A parts 列表提取文本 (只取 kind=text 的 part)。"""
    texts = []
    for p in parts or []:
        if isinstance(p, dict) and p.get("kind") == "text" and p.get("text"):
            texts.append(p["text"])
    return "\n".join(texts)


def _message_text(message: Any) -> str:
    if isinstance(message, dict):
        return _parts_text(message.get("parts"))
    return ""


def _consume_frame(acc: _Accumulator, result: Any) -> None:
    """把一帧 (task / message / statusUpdate / artifactUpdate) 喂给聚合器。

    优先按 kind 字段判断, 无 kind 时按结构推断 (Task 有 id+status,
    statusUpdate 有 taskId+status, message 有 parts, artifactUpdate 有 artifact)。
    """
    if not isinstance(result, dict):
        return
    kind = result.get("kind")
    if kind == "task" or (
        kind is None and "status" in result and "id" in result and "taskId" not in result
    ):
        acc.add_status(
            result.get("status"),
            task_id=result.get("id"),
            context_id=result.get("contextId"),
        )
        for artifact in result.get("artifacts") or []:
            acc.add_artifact(artifact)
    elif kind == "message" or (kind is None and "parts" in result and "status" not in result):
        if result.get("contextId"):
            acc.context_id = result["contextId"]
        acc.add_message(result)
    elif kind == "status-update" or "status" in result:
        acc.add_status(
            result.get("status"),
            task_id=result.get("taskId"),
            context_id=result.get("contextId"),
        )
    elif kind == "artifact-update" or "artifact" in result:
        acc.add_artifact(
            result.get("artifact"),
            task_id=result.get("taskId"),
            context_id=result.get("contextId"),
        )


def _message_params(
    text: str, *, context_id: str | None = None, task_id: str | None = None
) -> dict[str, Any]:
    """构造 message/stream 与 message/send 共用的 params。"""
    message: dict[str, Any] = {
        "messageId": str(uuid.uuid4()),
        "role": "user",
        "parts": [{"kind": "text", "text": text}],
    }
    if context_id:
        message["contextId"] = context_id
    if task_id:
        message["taskId"] = task_id
    return {"message": message}


def _raise_rpc_error(data: dict[str, Any]) -> None:
    err = data.get("error") or {}
    raise A2AError(err.get("message", "A2A error"), code=err.get("code"), data=err.get("data"))


class A2AClient:
    """单个 A2A agent endpoint 的客户端。

    用法:
        client = A2AClient(url, headers={"Authorization": "Bearer ..."})
        result = await client.send_message_streaming("任务文本", context_id=..., task_id=...)
        await client.close()
    """

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.url = url
        self._headers = {"Content-Type": "application/json", **(headers or {})}
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._req_id = 0

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, transport=self._transport)
        return self._client

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _payload(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params}

    async def _post(self, payload: dict[str, Any], *, accept: str) -> httpx.Response:
        client = await self._ensure_client()
        headers = {**self._headers, "Accept": accept}
        resp = await client.post(self.url, json=payload, headers=headers)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                if body.get("error"):
                    _raise_rpc_error(body)
            except (json.JSONDecodeError, ValueError):
                pass
            raise A2AError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return resp

    async def send_message_streaming(
        self,
        text: str,
        *,
        context_id: str | None = None,
        task_id: str | None = None,
    ) -> A2AResult:
        """message/stream: 消费 SSE 流聚合成一个 A2AResult。

        server 不支持流式 (返回非 SSE 内容 / JSON-RPC method not found) 时
        自动回退 message/send 非流式。
        """
        params = _message_params(text, context_id=context_id, task_id=task_id)
        resp = await self._post(self._payload("message/stream", params), accept="text/event-stream")

        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            acc = _Accumulator()
            for data in _parse_sse_events(resp.text):
                if data.get("error"):
                    _raise_rpc_error(data)
                _consume_frame(acc, data.get("result"))
            return acc.result()

        # 非 SSE 响应: 尝试按普通 JSON-RPC 解析
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            # 完全不是 JSON: server 不认流式, 回退非流式
            return await self.send_message(text, context_id=context_id, task_id=task_id)
        if data.get("error"):
            code = (data["error"] or {}).get("code")
            if code == -32601:  # method not found → 回退非流式
                return await self.send_message(text, context_id=context_id, task_id=task_id)
            _raise_rpc_error(data)
        # 部分 server 对流式请求直接回完整 result: 按 message/send 结果聚合
        acc = _Accumulator()
        _consume_frame(acc, data.get("result"))
        return acc.result()

    async def send_message(
        self,
        text: str,
        *,
        context_id: str | None = None,
        task_id: str | None = None,
    ) -> A2AResult:
        """message/send 非流式: result 是一个 Task 或 Message。"""
        params = _message_params(text, context_id=context_id, task_id=task_id)
        resp = await self._post(self._payload("message/send", params), accept="application/json")
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise A2AError(f"非 JSON 响应: {resp.text[:300]}") from e
        if data.get("error"):
            _raise_rpc_error(data)
        acc = _Accumulator()
        _consume_frame(acc, data.get("result"))
        return acc.result()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _parse_sse_events(text: str) -> list[dict[str, Any]]:
    """解析 SSE 流的 data: 行为 JSON-RPC envelope 列表。"""
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if not data_str:
            continue
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            events.append(data)
    return events


async def send_message_streaming(
    endpoint: str,
    text: str,
    *,
    context_id: str | None = None,
    task_id: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> A2AResult:
    """一次性便捷调用: message/stream 聚合结果 (失败时自动回退 message/send)。"""
    client = A2AClient(endpoint, headers=headers, timeout=timeout, transport=transport)
    try:
        return await client.send_message_streaming(text, context_id=context_id, task_id=task_id)
    finally:
        await client.close()
