"""工具路由器。


负责并发执行 LLM 请求的工具调用,捕获错误 (错误回填 is_error,不中断循环)。
"""

from __future__ import annotations

import asyncio
import traceback
from typing import Any

from operon.llm.messages import ToolResultBlock, ToolUseBlock

from .registry import ToolRegistry


class ToolRouter:
    """工具执行路由器。

    execute_tool_calls: 并发执行一批 tool_use,返回 tool_result 列表。
    错误处理: 单个工具失败 → 返回 is_error=True 的 result,不影响其他工具。
    超时: 每个工具独立超时。
    """

    def __init__(self, registry: ToolRegistry, *, default_timeout: float = 30.0):
        self.registry = registry
        self.default_timeout = default_timeout

    async def execute_one(
        self, tool_use: ToolUseBlock, *, timeout: float | None = None
    ) -> ToolResultBlock:
        """执行单个工具调用。

        """
        tool = self.registry.get(tool_use.name)
        if tool is None:
            return ToolResultBlock(
                tool_use_id=tool_use.id,
                content=(
                    f"Error: unknown tool '{tool_use.name}'. "
                    f"Available: {self.registry.names()}"
                ),
                is_error=True,
            )

        try:
            result = await asyncio.wait_for(
                tool.handler(**tool_use.input),
                timeout=timeout or self.default_timeout,
            )
            content = self._format_result(result)
            return ToolResultBlock(tool_use_id=tool_use.id, content=content, is_error=False)
        except TimeoutError:
            return ToolResultBlock(
                tool_use_id=tool_use.id,
                content=(
                    f"Error: tool '{tool_use.name}' timed out after "
                    f"{timeout or self.default_timeout}s"
                ),
                is_error=True,
            )
        except Exception as e:
            tb = traceback.format_exc()
            return ToolResultBlock(
                tool_use_id=tool_use.id,
                content=f"Error in tool '{tool_use.name}': {type(e).__name__}: {e}\n{tb}",
                is_error=True,
            )

    async def execute_tool_calls(
        self, tool_uses: list[ToolUseBlock], *, timeout: float | None = None
    ) -> list[ToolResultBlock]:
        """并发执行一批工具调用。

        原版行为: 工具并发执行,结果按 tool_use 顺序对齐回填。
        """
        if not tool_uses:
            return []
        results = await asyncio.gather(
            *[self.execute_one(tu, timeout=timeout) for tu in tool_uses]
        )
        return list(results)

    @staticmethod
    def _format_result(result: Any) -> str:
        """格式化工具结果为字符串 (给 LLM)。"""
        if isinstance(result, str):
            return result
        import json

        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(result)
