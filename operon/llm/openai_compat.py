"""OpenAI 兼容 LLM adapter。


重设计 (divergences §1): 适配国内模型 (DeepSeek/通义/智谱/Moonshot 等),
全部通过 base_url 切换。

用法:
    client = OpenAICompatClient(
        base_url="https://api.deepseek.com/v1",
        api_key="sk-...",
        model="deepseek-chat",
    )
    resp = await client.chat(messages, system="...", tools=[...])
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import LLMClient
from .message_adapter import (
    StreamAggregator,
    messages_to_openai,
    response_from_openai,
    tools_to_openai,
)
from .messages import LLMResponse, Message, ToolDefinition
from .token_counter import TokenCounter


class OpenAICompatClient(LLMClient):
    """OpenAI 兼容 API 客户端。

    适配任何兼容 OpenAI Chat Completions 协议的端点 (含国内模型)。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120.0,
        token_counter: TokenCounter | None = None,
        extra_headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = model
        self.token_counter = token_counter or TokenCounter()
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                **(extra_headers or {}),
            },
            timeout=timeout,
            transport=transport,
        )

    async def chat(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        max_tokens: int = 8192,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages_to_openai(messages, system),
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools_to_openai(tools)
            # 启用工具调用;多数国内模型接受 "auto"
            payload["tool_choice"] = kwargs.pop("tool_choice", "auto")
        payload.update(kwargs)

        resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        return response_from_openai(resp.json())

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        max_tokens: int = 8192,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式 chat。yield dict:
          {"type": "text", "delta": "..."}  — text 增量 (多次)
          {"type": "final", "response": LLMResponse}  — 流结束 (最后一次)

        对照 chat() (非流式), 区别是 payload 加 stream=True, 用 httpx stream 逐行读 SSE。
        tool_call arguments 是增量 JSON 字符串, 由 StreamAggregator 聚合。
        """
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages_to_openai(messages, system),
            "max_tokens": max_tokens,
            "stream": True,
        }
        # 部分兼容 provider 支持 stream_options.include_usage 拿 token 统计
        payload["stream_options"] = {"include_usage": True}
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools_to_openai(tools)
            payload["tool_choice"] = kwargs.pop("tool_choice", "auto")
        payload.update(kwargs)

        agg = StreamAggregator(model=model or self.default_model)
        # 用 client.stream 逐行读 SSE 响应
        async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                text_delta, reasoning_delta = agg.feed(chunk)
                if reasoning_delta:
                    yield {"type": "reasoning", "delta": reasoning_delta}
                if text_delta:
                    yield {"type": "text", "delta": text_delta}
        yield {"type": "final", "response": agg.finalize()}

    def count_tokens(self, text: str) -> int:
        return self.token_counter.count(text)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> OpenAICompatClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
