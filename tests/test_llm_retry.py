"""Tests for OpenAICompatClient retry behavior on 429 / 5xx / network errors."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from axiom_core.llm.messages import Message, Role
from axiom_core.llm.openai_compat import OpenAICompatClient


def _make_client(*, max_retries: int = 3, retry_backoff: float = 0.01) -> OpenAICompatClient:
    return OpenAICompatClient(
        base_url="https://example.com",
        api_key="sk-test",
        model="test-model",
        max_retries=max_retries,
        retry_backoff=retry_backoff,
    )


def _429_response(retry_after: str | None = None) -> httpx.Response:
    headers = {}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    req = httpx.Request("POST", "https://example.com/chat/completions")
    resp = httpx.Response(429, request=req, headers=headers, text="rate limited")
    return resp


def _200_response(content: dict) -> httpx.Response:
    req = httpx.Request("POST", "https://example.com/chat/completions")
    return httpx.Response(200, request=req, json=content)


@pytest.mark.asyncio
async def test_chat_retries_on_429_then_succeeds():
    client = _make_client()
    bad = _429_response()
    good = _200_response(
        {
            "choices": [
                {"message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )

    mock_post = AsyncMock(side_effect=[bad, good])
    client._client.post = mock_post  # type: ignore[method-assign]

    result = await client.chat([Message(role=Role.USER, content="hi")])
    assert result.content[0].text == "hello"
    assert mock_post.call_count == 2


@pytest.mark.asyncio
async def test_chat_honors_retry_after_header():
    client = _make_client(retry_backoff=10.0)
    bad = _429_response(retry_after="0.05")
    good = _200_response(
        {
            "choices": [
                {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )

    mock_post = AsyncMock(side_effect=[bad, good])
    client._client.post = mock_post  # type: ignore[method-assign]

    result = await client.chat([Message(role=Role.USER, content="hi")])
    assert result.content[0].text == "ok"
    assert mock_post.call_count == 2


@pytest.mark.asyncio
async def test_chat_gives_up_after_max_retries():
    client = _make_client(max_retries=2)
    bad = _429_response()

    mock_post = AsyncMock(side_effect=[bad, bad, bad])
    client._client.post = mock_post  # type: ignore[method-assign]

    with pytest.raises(httpx.HTTPStatusError):
        await client.chat([Message(role=Role.USER, content="hi")])
    assert mock_post.call_count == 3


@pytest.mark.asyncio
async def test_chat_does_not_retry_4xx_other_than_429():
    client = _make_client(max_retries=3)
    req = httpx.Request("POST", "https://example.com/chat/completions")
    bad = httpx.Response(400, request=req, text="bad request")
    mock_post = AsyncMock(return_value=bad)
    client._client.post = mock_post  # type: ignore[method-assign]

    with pytest.raises(httpx.HTTPStatusError):
        await client.chat([Message(role=Role.USER, content="hi")])
    assert mock_post.call_count == 1


@pytest.mark.asyncio
async def test_chat_stream_retries_on_429_then_succeeds():
    client = _make_client()
    attempt = 0

    @asynccontextmanager
    async def fake_stream(*args, **kwargs):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            req = httpx.Request("POST", "https://example.com/chat/completions")
            resp = httpx.Response(429, request=req, text="rate limited")
            raise httpx.HTTPStatusError("429", request=req, response=resp)

        class _Resp:
            async def aiter_lines(self) -> AsyncIterator[str]:
                for line in [
                    'data: {"choices":[{"delta":{"content":"hi"}}]}',
                    "data: [DONE]",
                ]:
                    yield line

            def raise_for_status(self) -> None:
                pass

        yield _Resp()

    client._client.stream = fake_stream  # type: ignore[method-assign]

    chunks = []
    async for chunk in client.chat_stream([Message(role=Role.USER, content="hello")]):
        chunks.append(chunk)

    assert attempt == 2
    assert any(c.get("type") == "text" and c.get("delta") == "hi" for c in chunks)
    assert chunks[-1]["type"] == "final"


@pytest.mark.asyncio
async def test_chat_stream_does_not_retry_after_first_chunk():
    client = _make_client(max_retries=3)

    @asynccontextmanager
    async def fake_stream(*args, **kwargs):
        class _Resp:
            async def aiter_lines(self) -> AsyncIterator[str]:
                yield 'data: {"choices":[{"delta":{"content":"hi"}}]}'
                req = httpx.Request("POST", "https://example.com/chat/completions")
                resp = httpx.Response(503, request=req, text="unavailable")
                raise httpx.HTTPStatusError("503", request=req, response=resp)

            def raise_for_status(self) -> None:
                pass

        yield _Resp()

    client._client.stream = fake_stream  # type: ignore[method-assign]

    chunks = []
    with pytest.raises(httpx.HTTPStatusError):
        async for chunk in client.chat_stream([Message(role=Role.USER, content="hello")]):
            chunks.append(chunk)

    # 只拿到第一块, 不重试
    assert len(chunks) == 1
