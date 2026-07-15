"""Plan mode 工具: generate_plan / update_step_status / approve_plan。

对应原版: 0808.js:10 (generate_plan) + 0860.js:1011 (update_step_status)。
Plan mode 是 agent 三模式之一: 强制先计划后执行。

流程 (对照 explore 报告):
1. plan_mode 开启时,agent 必须先调 generate_plan 生成计划
2. 生成后 frame status → awaiting_plan_approval
3. 用户 approve_plan → plan.approved=True, status → processing, 继续
4. 执行中调 update_step_status 更新步骤状态

门控 _gate_plan_produce_denial (0871.js:1625): 若 plan mode 开启但未生成计划,
拒绝 end_turn,最多拒绝 3 次后放行。
"""

from __future__ import annotations

from typing import Any

from operon.agent.states import FrameStatus
from operon.tools.context import ToolContext


async def generate_plan(
    ctx: ToolContext,
    steps: list[dict[str, Any]] | None = None,
    summary: str | None = None,
) -> str:
    """生成执行计划。

    对应原版 generate_plan (0808.js:10): 写 plan artifact,触发 awaiting_plan_approval。
    steps: [{id, description}] 列表。
    """
    if steps is None:
        steps = []
    # 规范化 step 结构
    normalized = []
    for i, s in enumerate(steps):
        sid = s.get("id") or f"step_{i + 1}"
        normalized.append(
            {
                "id": sid,
                "description": s.get("description", ""),
                "status": "pending",
            }
        )

    ctx.plan.steps = normalized
    ctx.plan.plan_artifact_id = f"plan_{ctx.frame.id[:8]}"

    # 触发 awaiting_plan_approval (原版行为)
    ctx.frame_service.update_status(ctx.frame.id, FrameStatus.AWAITING_PLAN_APPROVAL)

    plan_text = summary or "Execution plan:"
    lines = [plan_text, ""]
    for s in normalized:
        lines.append(f"  [{s['id']}] {s['description']}")
    return "\n".join(lines) + "\n\nPlan generated. Awaiting approval."


async def update_step_status(
    ctx: ToolContext,
    step_id: str,
    status: str,
    note: str | None = None,
) -> str:
    """更新计划步骤状态。

    对应原版 update_step_status (0860.js:1011)。
    status: pending | in_progress | completed | skipped
    """
    if not ctx.plan.steps:
        return "Error: no plan exists. Call generate_plan first."

    step = ctx.plan.find_step(step_id)
    if step is None:
        return f"Error: step '{step_id}' not found. Available: {[s['id'] for s in ctx.plan.steps]}"

    valid = {"pending", "in_progress", "completed", "skipped"}
    if status not in valid:
        return f"Error: invalid status '{status}'. Valid: {valid}"

    step["status"] = status
    if note:
        step["note"] = note
    return f"Step {step_id} → {status}"


async def approve_plan(ctx: ToolContext) -> str:
    """批准计划 (供用户审批后调用)。"""
    if not ctx.plan.steps:
        return "Error: no plan to approve"
    ctx.plan.approved = True
    # 恢复 processing
    if ctx.frame.status == FrameStatus.AWAITING_PLAN_APPROVAL:
        # update_status 不允许从 awaiting 转 processing (awaiting 不在 TERMINAL,
        # 但原版 awaiting→processing 是合法的),直接设置
        ctx.frame.status = FrameStatus.PROCESSING
    return f"Plan approved ({len(ctx.plan.steps)} steps). Proceeding."


def get_plan_summary(ctx: ToolContext) -> str:
    """计划摘要 (注入 dynamic prompt 用)。"""
    if not ctx.plan.steps:
        return ""
    lines = ["## Plan Steps"]
    for s in ctx.plan.steps:
        mark = {"pending": "○", "in_progress": "◑", "completed": "●", "skipped": "✕"}.get(
            s["status"], "?"
        )
        lines.append(f"  {mark} [{s['id']}] {s['description']} ({s['status']})")
    return "\n".join(lines)


GENERATE_PLAN_SPEC = {
    "name": "generate_plan",
    "description": (
        "Generate an execution plan as a list of steps. REQUIRED first action when plan mode is active. "
        "After calling this, the plan must be approved before execution proceeds."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Step identifier"},
                        "description": {"type": "string", "description": "What this step does"},
                    },
                },
                "description": "Ordered list of plan steps",
            },
            "summary": {"type": "string", "description": "Brief plan summary"},
        },
        "required": ["steps"],
    },
}

UPDATE_STEP_STATUS_SPEC = {
    "name": "update_step_status",
    "description": "Update the status of a plan step. Use during execution to track progress.",
    "parameters": {
        "type": "object",
        "properties": {
            "step_id": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed", "skipped"],
            },
            "note": {"type": "string", "description": "Optional note"},
        },
        "required": ["step_id", "status"],
    },
}
