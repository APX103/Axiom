"""Phase 3: A2A 协议客户端测试。

覆盖:
1. message/stream SSE 流聚合 (task / message / statusUpdate / artifactUpdate 四种帧)
2. input-required 终结态
3. 非流式回退 (server 不支持流式 → message/send)
4. message/send 直接聚合 (Task 结果含 artifacts)
5. JSON-RPC error 传播
6. context_id/task_id 参数传递

全部用 httpx.MockTransport 模拟 SSE 流, 无真实网络 (写法同 MCP SSE 测试)。
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from axiom_core.a2a.client import A2AClient, A2AError, send_message_streaming

ENDPOINT = "https://agent.example.com/a2a"


def _sse_body(*frames: dict) -> str:
    """把若干 result 帧包成 SSE 流 (每帧一个 data: 行, JSON-RPC envelope)。"""
    return "".join(
        f"data: {json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': f})}\n\n" for f in frames
    )


def _sse_response(*frames: dict) -> httpx.Response:
    return httpx.Response(
        200, text=_sse_body(*frames), headers={"content-type": "text/event-stream"}
    )


def _task_frame(state: str = "submitted", task_id: str = "t-1", context_id: str = "ctx-1") -> dict:
    return {
        "kind": "task",
        "id": task_id,
        "contextId": context_id,
        "status": {"state": state},
    }


# ---------- 1. SSE 流聚合 (四种帧) ----------


@pytest.mark.asyncio
async def test_streaming_aggregates_all_frame_kinds():
    """task + statusUpdate(working) + artifactUpdate + message + statusUpdate(completed)。"""
    frames = [
        _task_frame("submitted"),
        {
            "kind": "status-update",
            "taskId": "t-1",
            "contextId": "ctx-1",
            "status": {"state": "working"},
        },
        {
            "kind": "artifact-update",
            "taskId": "t-1",
            "contextId": "ctx-1",
            "artifact": {
                "artifactId": "a-1",
                "name": "code",
                "parts": [{"kind": "text", "text": "ARTIFACT_TEXT"}],
            },
        },
        {
            "kind": "message",
            "messageId": "m-1",
            "role": "agent",
            "contextId": "ctx-1",
            "parts": [{"kind": "text", "text": "MESSAGE_TEXT"}],
        },
        {
            "kind": "status-update",
            "taskId": "t-1",
            "contextId": "ctx-1",
            "status": {
                "state": "completed",
                "message": {
                    "messageId": "m-2",
                    "role": "agent",
                    "parts": [{"kind": "text", "text": "FINAL_TEXT"}],
                },
            },
            "final": True,
        },
    ]
    transport = httpx.MockTransport(lambda req: _sse_response(*frames))
    client = A2AClient(ENDPOINT, transport=transport)

    result = await client.send_message_streaming("帮我写代码")

    assert result.state == "completed"
    assert result.context_id == "ctx-1"
    assert result.task_id == "t-1"
    assert result.error is None
    # 三种文本来源都拼进来 (按到达顺序)
    assert result.text == "ARTIFACT_TEXT\nMESSAGE_TEXT\nFINAL_TEXT"
    await client.close()


# ---------- 2. input-required 终结态 ----------


@pytest.mark.asyncio
async def test_streaming_input_required_terminal():
    frames = [
        _task_frame("submitted"),
        {
            "kind": "status-update",
            "taskId": "t-9",
            "contextId": "ctx-9",
            "status": {
                "state": "input-required",
                "message": {
                    "messageId": "m-1",
                    "role": "agent",
                    "parts": [{"kind": "text", "text": "你想要哪种风格?"}],
                },
            },
            "final": True,
        },
    ]
    transport = httpx.MockTransport(lambda req: _sse_response(*frames))
    client = A2AClient(ENDPOINT, transport=transport)

    result = await client.send_message_streaming("写首诗")

    assert result.state == "input-required"
    assert result.text == "你想要哪种风格?"
    assert result.context_id == "ctx-9"
    assert result.task_id == "t-9"
    assert result.error is None  # input-required 不是错误
    await client.close()


# ---------- 3. 非流式回退 ----------


@pytest.mark.asyncio
async def test_fallback_to_message_send_on_method_not_found():
    """message/stream 返回 JSON-RPC -32601 → 自动回退 message/send。"""
    seen_methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen_methods.append(body["method"])
        if body["method"] == "message/stream":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "error": {"code": -32601, "message": "Method not found"},
                },
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "kind": "task",
                    "id": "t-2",
                    "contextId": "ctx-2",
                    "status": {"state": "completed"},
                    "artifacts": [
                        {"artifactId": "a-1", "parts": [{"kind": "text", "text": "DONE"}]}
                    ],
                },
            },
        )

    client = A2AClient(ENDPOINT, transport=httpx.MockTransport(handler))
    result = await client.send_message_streaming("任务")

    assert seen_methods == ["message/stream", "message/send"]
    assert result.state == "completed"
    assert result.text == "DONE"
    assert result.context_id == "ctx-2"
    await client.close()


@pytest.mark.asyncio
async def test_fallback_to_message_send_on_non_json_response():
    """message/stream 返回非 JSON 非 SSE 内容 → 回退 message/send。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "message/stream":
            return httpx.Response(200, text="<html>unsupported</html>")
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "kind": "message",
                    "messageId": "m-1",
                    "role": "agent",
                    "parts": [{"kind": "text", "text": "SEND_OK"}],
                },
            },
        )

    client = A2AClient(ENDPOINT, transport=httpx.MockTransport(handler))
    result = await client.send_message_streaming("任务")

    assert result.state == "unknown"  # message 帧不带 state
    assert result.text == "SEND_OK"
    await client.close()


# ---------- 4. message/send 直接聚合 ----------


@pytest.mark.asyncio
async def test_send_message_task_result_with_artifacts():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["method"] == "message/send"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "kind": "task",
                    "id": "t-3",
                    "contextId": "ctx-3",
                    "status": {"state": "failed", "message": {
                        "messageId": "m-1",
                        "role": "agent",
                        "parts": [{"kind": "text", "text": "执行出错"}],
                    }},
                    "artifacts": [
                        {"artifactId": "a-1", "parts": [{"kind": "text", "text": "partial"}]}
                    ],
                },
            },
        )

    client = A2AClient(ENDPOINT, transport=httpx.MockTransport(handler))
    result = await client.send_message("任务")

    assert result.state == "failed"
    assert result.error is not None  # failed 时 error 与 text 同步
    assert "执行出错" in result.text
    assert "partial" in result.text
    await client.close()


# ---------- 5. JSON-RPC error 传播 ----------


@pytest.mark.asyncio
async def test_json_rpc_error_in_stream_raises():
    body = "data: " + json.dumps(
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}}
    ) + "\n\n"
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream"}
        )
    )
    client = A2AClient(ENDPOINT, transport=transport)

    with pytest.raises(A2AError, match="boom") as exc_info:
        await client.send_message_streaming("任务")
    assert exc_info.value.code == -32000
    await client.close()


@pytest.mark.asyncio
async def test_http_error_raises():
    transport = httpx.MockTransport(lambda req: httpx.Response(401, text="unauthorized"))
    client = A2AClient(ENDPOINT, transport=transport)

    with pytest.raises(A2AError, match="HTTP 401"):
        await client.send_message_streaming("任务")
    await client.close()


# ---------- 6. context_id / task_id 参数传递 ----------


@pytest.mark.asyncio
async def test_context_and_task_id_passed_in_params():
    """续接参数进 message; messageId 是合法 uuid。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["message"] = body["params"]["message"]
        seen["accept"] = request.headers.get("accept", "")
        return _sse_response(
            {
                "kind": "status-update",
                "taskId": "t-9",
                "contextId": "ctx-9",
                "status": {"state": "completed"},
                "final": True,
            }
        )

    result = await send_message_streaming(
        ENDPOINT,
        "我的补充输入",
        context_id="ctx-9",
        task_id="t-9",
        headers={"X-Custom": "1"},
        transport=httpx.MockTransport(handler),
    )

    message = seen["message"]
    assert message["role"] == "user"
    assert message["contextId"] == "ctx-9"
    assert message["taskId"] == "t-9"
    assert message["parts"] == [{"kind": "text", "text": "我的补充输入"}]
    uuid.UUID(message["messageId"])  # 合法 uuid, 否则抛 ValueError
    assert "text/event-stream" in seen["accept"]
    assert result.state == "completed"


@pytest.mark.asyncio
async def test_no_context_task_id_omitted():
    """不传时 params 里不含 contextId/taskId 键。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["message"] = json.loads(request.content)["params"]["message"]
        return _sse_response(_task_frame("completed"))

    client = A2AClient(ENDPOINT, transport=httpx.MockTransport(handler))
    await client.send_message_streaming("任务")

    assert "contextId" not in seen["message"]
    assert "taskId" not in seen["message"]
    await client.close()
