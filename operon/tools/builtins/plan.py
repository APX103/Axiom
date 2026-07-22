"""Plan mode 工具: generate_plan / update_step_status / approve_plan。


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
    research_question: str | None = None,
    scope: str | None = None,
    desired_outputs: list[str] | None = None,
    feasibility: dict[str, Any] | None = None,
) -> str:
    """生成执行计划。


    steps: [{id, description}] 列表。
    research_question: 本次任务要回答的核心问题 (一句话)。综述/调研类任务必填 ——
        它是防止后半段跑偏的锚点, 每轮 prompt 都会重新注入。
    scope: 范围边界 (涵盖什么/不涵盖什么)。
    desired_outputs: 期望的最终交付物清单。
    feasibility: {confidence: high|medium|low, rationale: str} 可行性评估。
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
    ctx.plan.approved = False
    ctx.plan.plan_artifact_id = f"plan_{ctx.frame.id[:8]}"
    # 收敛锚点
    ctx.plan.research_question = research_question
    ctx.plan.scope = scope
    ctx.plan.desired_outputs = list(desired_outputs) if desired_outputs else []
    ctx.plan.feasibility = feasibility

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
    # 批准即进入执行态。即使模型尚未主动调用 update_step_status，UI 也应
    # 立即显示一个明确的当前步骤，而不是让整份计划一直停在 pending。
    if not any(s.get("status") == "in_progress" for s in ctx.plan.steps):
        first_pending = next(
            (s for s in ctx.plan.steps if s.get("status") == "pending"),
            None,
        )
        if first_pending is not None:
            first_pending["status"] = "in_progress"
    # 恢复 processing
    if ctx.frame.status == FrameStatus.AWAITING_PLAN_APPROVAL:
        # update_status 不允许从 awaiting 转 processing (awaiting 不在 TERMINAL,
        # 但原版 awaiting→processing 是合法的),直接设置
        ctx.frame.status = FrameStatus.PROCESSING
    return f"Plan approved ({len(ctx.plan.steps)} steps). Proceeding."


def complete_unfinished_steps(ctx: ToolContext) -> bool:
    """成功结束任务时收口仍未完成的步骤。

    模型应在执行中精确调用 ``update_step_status``；这里是生命周期兜底，确保
    Agent 已自然成功结束时，计划快照不会仍显示 pending/in_progress。
    返回是否发生了状态变化，供调用方决定是否推送实时事件。
    """
    changed = False
    for step in ctx.plan.steps:
        if step.get("status") in {"pending", "in_progress"}:
            step["status"] = "completed"
            changed = True
    return changed


def get_plan_summary(ctx: ToolContext) -> str:
    """计划摘要 (注入 dynamic prompt 用)。

    收敛锚点 (research_question/scope/desired_outputs) 渲染在摘要最前 ——
    长对话里原始问题会被滚动摘要冲淡, 这里每轮重新注入, 作为后续章节的"标尺"。
    """
    if not ctx.plan.steps and not ctx.plan.research_question:
        return ""
    lines = ["## Plan Steps"]
    # 收敛锚点优先展示
    if ctx.plan.research_question:
        lines.append("### Research Question (do not drift from this)")
        lines.append(f"  {ctx.plan.research_question}")
    if ctx.plan.scope:
        lines.append(f"  Scope: {ctx.plan.scope}")
    if ctx.plan.desired_outputs:
        lines.append(f"  Desired outputs: {', '.join(ctx.plan.desired_outputs)}")
    if ctx.plan.feasibility:
        conf = ctx.plan.feasibility.get("confidence", "?")
        rat = ctx.plan.feasibility.get("rationale", "")
        lines.append(f"  Feasibility: {conf}" + (f" — {rat}" if rat else ""))
    if ctx.plan.steps:
        lines.append("")
        for s in ctx.plan.steps:
            mark = {"pending": "○", "in_progress": "◑", "completed": "●", "skipped": "✕"}.get(
                s["status"], "?"
            )
            lines.append(f"  {mark} [{s['id']}] {s['description']} ({s['status']})")
    return "\n".join(lines)


GENERATE_PLAN_SPEC = {
    "name": "generate_plan",
    "description": (
        "Generate an execution plan as a list of steps. "
        "REQUIRED first action when plan mode is active. "
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
            "research_question": {
                "type": "string",
                "description": (
                    "The core question this task answers, in one sentence. "
                    "REQUIRED for surveys/reviews and any open-ended research task — "
                    "it anchors every later section and prevents drift. "
                    "e.g. 'What methods improve LLM reasoning, and how do they compare?'"
                ),
            },
            "scope": {
                "type": "string",
                "description": (
                    "Boundary of the work: what's in scope and what's explicitly out."
                ),
            },
            "desired_outputs": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Final deliverables (e.g. ['LaTeX survey paper', 'references.bib'])."
                ),
            },
            "feasibility": {
                "type": "object",
                "properties": {
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "rationale": {"type": "string"},
                },
                "description": "Feasibility assessment with confidence level and rationale.",
            },
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
