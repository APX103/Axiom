"""把 agent 回调桥接到 WebSocket 的适配器。


本项目: AgentCallbacks 子类把每次回调序列化为 dict,经 WS 推给前端。
事件协议见 axiom_core.api.events。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from axiom_core.agent.runner import AgentCallbacks
from axiom_core.api.events import plan_snapshot
from axiom_core.llm.messages import ToolResultBlock, ToolUseBlock

logger = logging.getLogger(__name__)


class WSCallbacks(AgentCallbacks):
    """agent 回调 → WebSocket 事件。

    事件放入 asyncio.Queue,由 ws 消费者协程发到前端。
    complete 事件由 router 在 run 结束时统一塞入 (带完整 plan/artifacts 快照)。
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)

    async def _emit(self, event: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            etype = event.get("type", "?")
            if etype == "complete":
                # complete 是流终止信号, 绝不能丢: 腾出最旧的事件再塞入。
                # 否则 SSE 消费者永远等不到结束, 前端会永远卡在 running。
                logger.error(
                    "event queue full, evicting oldest events to deliver complete "
                    "(qsize=%d)",
                    self.queue.qsize(),
                )
                while True:
                    try:
                        self.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        self.queue.put_nowait(event)
                        return
                    except asyncio.QueueFull:
                        continue
            else:
                # 队列满时丢弃增量事件 (避免阻塞 agent 循环), 但必须可见。
                logger.warning(
                    "event queue full, dropping event type=%s (qsize=%d)",
                    etype,
                    self.queue.qsize(),
                )

    # ---- AgentCallbacks 实现 ----

    async def on_start(self, frame) -> None:
        await self._emit(
            {"type": "start", "frame_id": frame.id, "task_summary": frame.task_summary or ""}
        )

    async def on_iteration(self, n: int) -> None:
        await self._emit({"type": "iteration", "n": n})

    async def on_assistant_thinking(self, text: str) -> None:
        if text:
            await self._emit({"type": "thinking", "text": text})

    async def on_assistant_text(self, text: str) -> None:
        if text.strip():
            await self._emit({"type": "text", "text": text})

    async def on_tool_calls(self, tool_uses: list[ToolUseBlock]) -> None:
        await self._emit(
            {
                "type": "tool_calls",
                "calls": [{"id": tu.id, "name": tu.name, "input": tu.input} for tu in tool_uses],
            }
        )

    async def on_tool_results(self, results: list[ToolResultBlock]) -> None:
        await self._emit(
            {
                "type": "tool_results",
                "results": [
                    {
                        "tool_use_id": r.tool_use_id,
                        "content": r.content if isinstance(r.content, str) else str(r.content),
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            }
        )

    async def on_plan_update(self, plan: Any) -> None:
        """推送执行中的完整 plan 快照，供前端实时刷新步骤状态。"""
        snapshot = plan_snapshot(plan)
        if snapshot is not None:
            await self._emit({"type": "plan_update", "plan": snapshot})

    async def on_event(self, event: str, detail: str) -> None:
        await self._emit({"type": "notice", "event": event, "detail": detail})

    async def on_complete(self, final_text: str) -> None:
        pass  # router 统一发 complete

    async def emit_complete(self, result: Any, ctx: Any) -> None:
        """run 结束后发 complete 事件 (带 plan/artifacts 快照)。"""
        current_plan = None
        if ctx is not None and ctx.plan.steps:
            current_plan = plan_snapshot(ctx.plan)
        # ask_user 触发 awaiting=user_response 时, 把问题/选项带给前端渲染选择框。
        # 用户回答后这些会被清掉。
        pending_ask = None
        if result.awaiting == "user_response" and ctx is not None:
            pa = getattr(ctx, "pending_ask", None)
            if pa is not None:
                pending_ask = {"question": pa.question, "options": list(pa.options)}
        await self._emit(
            {
                "type": "complete",
                "kind": result.kind.value,
                "final_text": result.final_text,
                "awaiting": result.awaiting,
                "pending_ask": pending_ask,
                "error": result.error,
                "usage": result.usage,
                "iterations": result.iterations,
                "frame_status": result.frame.status.value,
                "plan": current_plan,
                "artifacts": ctx.artifacts if ctx else {},
            }
        )
