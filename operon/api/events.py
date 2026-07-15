"""API WebSocket 事件协议。

前后端共享的事件契约。agent 循环的回调 → WS 事件 → 前端渲染。
每个事件是一个 JSON dict {type, ...}。

事件类型对照 agent 回调 (operon.agent.runner.AgentCallbacks):
- start:        会话开始 (含 frame_id)
- iteration:    新一轮开始
- text:         assistant 文本块
- tool_calls:   模型请求的工具调用
- tool_results: 工具执行结果
- event:        通用事件 (plan_denial / max_tokens 等)
- complete:     会话完成 (含 final_text / kind / usage)
- error:        错误
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class WSEvent(BaseModel):
    """WebSocket 事件基类。type 字段区分。"""

    type: str


class StartEvent(BaseModel):
    type: Literal["start"] = "start"
    frame_id: str
    task_summary: str
    plan_mode: bool


class IterationEvent(BaseModel):
    type: Literal["iteration"] = "iteration"
    n: int


class TextEvent(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ThinkingEvent(BaseModel):
    type: Literal["thinking"] = "thinking"
    text: str


class ToolCallEvent(BaseModel):
    type: Literal["tool_calls"] = "tool_calls"
    calls: list[dict[str, Any]]  # [{id, name, input}]


class ToolResultEvent(BaseModel):
    type: Literal["tool_results"] = "tool_results"
    results: list[dict[str, Any]]  # [{tool_use_id, content, is_error}]


class NoticeEvent(BaseModel):
    type: Literal["notice"] = "notice"
    event: str  # plan_denial / max_tokens / ...
    detail: str


class CompleteEvent(BaseModel):
    type: Literal["complete"] = "complete"
    kind: str  # natural / awaiting / max_iters / error / cancelled
    final_text: str = ""
    awaiting: str | None = None
    error: str | None = None
    usage: dict[str, int] = {}
    iterations: int = 0
    # 完成时的 frame 状态快照 (供前端更新 UI)
    frame_status: str = "completed"
    plan: dict[str, Any] | None = None  # {steps: [...], approved: bool}
    artifacts: dict[str, Any] = {}  # 工作区产物 {path: {size, ...}}


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str
