"""submit_output 工具 — 子 agent 结构化返回结果。

子 agent (由 delegate 工具派生) 用此工具提交结构化产出。
delegate handler 从子 frame 的 context 里读出, 返回给父 agent。
"""

from __future__ import annotations

from operon.tools.context import ToolContext


async def submit_output(
    ctx: ToolContext,
    output: dict | None = None,
    completion_bullets: list[str] | None = None,
) -> str:
    """提交本次子任务的产出。调用后子 agent 结束。

    output: 结构化结果 (匹配 delegate 时传入的 output_schema)。
    completion_bullets: 2-4 条过去时摘要 (做了什么)。
    """
    ctx.frame.context["_submitted_output"] = {
        "output": output or {},
        "completion_bullets": completion_bullets or [],
    }
    # 标记子 agent 应结束 (runner 检测此标记后自然完成)
    ctx.frame.context["_submit_output_called"] = True
    return "Output submitted. Ending subtask."


SUBMIT_OUTPUT_SPEC = {
    "name": "submit_output",
    "description": (
        "Submit the structured result of this subtask. Call ONCE when the work is done. "
        "After calling this, the subtask ends and the result returns to the parent agent."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "output": {
                "type": "object",
                "description": (
                    "The structured result, matching the output_schema "
                    "given in the task."
                ),
            },
            "completion_bullets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-4 past-tense summaries of what was accomplished.",
            },
        },
    },
}
