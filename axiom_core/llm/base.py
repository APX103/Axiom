"""LLM Client Provider 抽象。


重设计点 (divergences §1): 原版只接 Anthropic,本项目提供 Provider 抽象,
首版实现 OpenAI 兼容 adapter 适配国内模型。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from .messages import LLMResponse, Message, ToolDefinition


class LLMClient(ABC):
    """LLM 客户端协议。

    所有 provider (OpenAI 兼容、未来可加 Anthropic/本地) 都实现此接口。
    Agent 状态机只依赖此接口,不感知具体 provider。
    """

    @abstractmethod
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
        """发起一次对话调用。

        Args:
            messages: 对话历史 (不含 system,system 单独传)
            system: system prompt (原版 floor+stable+dynamic 拼装结果)
            tools: 可用工具定义
            model: 模型名 (None 则用 client 默认)
            max_tokens: 输出上限
            temperature: 采样温度
        Returns:
            统一的 LLMResponse
        """
        ...

    async def chat_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        """流式 chat (可选实现)。yield {"type":"text","delta"} 和 {"type":"final","response"}。

        基类默认 raise NotImplementedError; 支持流式的子类 (如 OpenAICompatClient) 覆盖。
        runner 在尝试流式前先判断 hasattr(client, "chat_stream") 且未被子类用默认实现。
        """
        raise NotImplementedError("this LLM client does not support streaming")
        # 让类型检查认为这是 async generator (不可达)
        yield {}  # type: ignore[unreachable]

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """估算文本 token 数。


        Rolling Compact 用此估算消息 token 以决定压缩时机。
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """释放资源。"""
        ...
