"""boundary 工具: 标记任务边界, 让 Rolling Compact 在任务之间切而非任务中间切。



agent 完成一个任务阶段后调用此工具, 在消息流里插入一个 task_boundary 标记。
chunk.py 的 _is_task_boundary 检测到此标记后, 会把 L1 chunk 的边界对齐到这里,
避免压缩把一个正在进行的任务从中间截断。
"""

from __future__ import annotations

from operon.tools.context import ToolContext

BOUNDARY_SPEC = {
    "name": "boundary",
    "description": (
        "Mark a task transition in the conversation — a hint to the harness "
        "about where one piece of work ends and the next begins, so that when "
        "context is later summarized the chunk edge lands at a sensible point "
        "rather than mid-task. Call this once a distinct piece of work is "
        "complete (e.g. finished a search, completed an analysis, saved a "
        "deliverable). The label is a note to your future self about what "
        "just closed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": "Short label for what just completed (e.g. 'literature search', 'data analysis').",
            },
        },
        "required": [],
    },
}


async def boundary(ctx: ToolContext, *, label: str | None = None) -> str:
    """插入任务边界标记。"""
    from operon.llm.messages import Message

    msg = Message(
        role="user",
        content=f"[boundary] {label or 'task transition'}",
        task_boundary={"label": label or ""},
        # 内部上下文管理标记, 不应渲染给用户 (重载 session 时会被前端 harness_notice 过滤器跳过)
        _harness_notice=True,
    )
    ctx.frame.messages.append(msg)
    return f"Boundary marked: {label or 'task transition'}"
