"""工具注册表。


每个工具是 {name, description, parameters, handler},
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from operon.llm.messages import ToolDefinition

ToolHandler = Callable[..., Awaitable[Any]]


class ToolSpec(BaseModel):
    """工具规格。"""

    name: str
    description: str
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def to_definition(self) -> ToolDefinition:
        """转 LLM ToolDefinition。"""
        return ToolDefinition(
            name=self.name, description=self.description, parameters=self.parameters
        )


class RegisteredTool:
    """一个已注册的工具 = 规格 + handler。"""

    def __init__(self, spec: ToolSpec, handler: ToolHandler):
        self.spec = spec
        self.handler = handler

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def description(self) -> str:
        return self.spec.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self.spec.parameters


class ToolRegistry:
    """工具注册表。

    agent 启动时注册工具;循环中按 LLM 返回的 tool_use.name 查找 handler。
    按角色限制可见工具 (原版 REVIEWER 只能用只读工具集 i$z)。
    """

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
        handler: ToolHandler | None = None,
    ) -> None:
        """注册工具。"""
        spec = ToolSpec(
            name=name,
            description=description,
            parameters=parameters or {"type": "object", "properties": {}},
        )
        if handler is None:
            raise ValueError(f"tool {name} requires a handler")
        self._tools[name] = RegisteredTool(spec, handler)

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def definitions(self, *, allowed: list[str] | None = None) -> list:
        """返回给 LLM 的 ToolDefinition 列表。

        allowed: 白名单 (按角色限制,如 REVIEWER 只读)。None=全部。
        """
        names = allowed if allowed is not None else list(self._tools.keys())
        return [self._tools[n].spec.to_definition() for n in names if n in self._tools]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
