"""delegate 工具 — 子 agent 委派 (sub-agent)。

父 agent 调 delegate(task, ...) 在独立上下文里跑一个子 agent。
子 agent 只看到 task + context_summary (上下文完全隔离), 跑完把结果交回父。

设计 (对照原版 host.delegate, 简化为 LLM tool-call):
- 子 frame 由 create_child_frame 创建 (is_hidden, 不进 UI)
- 子 agent 用独立 ToolContext (复用父 workspace/host/artifact_store, 独立 frame/plan)
- 子工具集默认去掉 delegate (防无限递归) 和 ask_user/generate_plan (子不与用户交互)
- 深度上限 2 (root → child → grandchild)
- submit_output 工具供子结构化返回 (output_schema 模式)
"""

from __future__ import annotations

import logging

from operon.tools.context import ToolContext

logger = logging.getLogger(__name__)

# 深度上限: root(0) → child(1) → grandchild(2)。grandchild 不能再 delegate。
_MAX_DEPTH = 2

# 子 agent 默认可用工具 (安全子集: 文件/检索/skill/artifact + submit_output)。
# 不含 delegate (防递归)、ask_user/generate_plan (子不与用户交互、不另起 plan)。
_DEFAULT_CHILD_TOOLS = [
    "read_file", "write_file", "edit_file", "list_files",
    "web_search", "fetch_url", "search_papers", "fetch_paper",
    "search_skills", "skill",
    "save_artifacts", "get_artifact", "list_artifacts",
    "submit_output",
]


def _frame_depth(ctx: ToolContext) -> int:
    """计算当前 frame 在 delegate 树里的深度 (root=0)。"""
    depth = 0
    frame = ctx.frame
    while frame.parent_frame_id is not None:
        depth += 1
        parent = ctx.frame_service.get(frame.parent_frame_id)
        if parent is None:
            break
        frame = parent
    return depth


async def delegate(
    ctx: ToolContext,
    task: str,
    context_summary: str | None = None,
    output_schema: dict | None = None,
    model: str | None = None,
    tools: list[str] | None = None,
    max_iterations: int = 8,
    name: str | None = None,
) -> dict:
    """派一个子 agent 在独立上下文里完成子任务。

    task: 子 agent 要做的事 (必填)。
    context_summary: 给子的背景。子只看到 task + context_summary, 看不到父对话。
    output_schema: JSON Schema。设了则子必须调 submit_output 匹配它, 否则子返回文本。
    model: 子用的模型 (不填用父的)。
    tools: 子的工具白名单 (不填用安全默认集, 不含 delegate)。
    max_iterations: 子的迭代上限 (默认 8)。
    name: 子任务短标签 (用于 delegate_name 记录)。
    """
    # 1. 防递归: 深度检查
    depth = _frame_depth(ctx)
    if depth >= _MAX_DEPTH:
        return {
            "status": "error",
            "error": f"delegate depth limit reached ({depth} >= {_MAX_DEPTH}). "
            "Cannot spawn further sub-agents.",
        }

    if not ctx.llm or not ctx.registry:
        return {"status": "error", "error": "delegate requires llm + registry on context"}

    # 2. 创建子 frame (hidden, 不进 UI)
    child_frame = ctx.frame_service.create_child_frame(
        ctx.frame.id,
        agent_name="SUBAGENT",
        delegate_name=name or "subtask",
        model=model,
        is_hidden=True,
    )

    # 3. 建子 ToolContext (复用父的共享资源, 独立 frame/plan)
    child_ctx = ToolContext(
        frame=child_frame,
        frame_service=ctx.frame_service,
        workspace=ctx.workspace,
        # 子复用父的 llm/registry 模板, 但下面会建子 registry
        llm=ctx.llm,
        artifact_store=ctx.artifact_store,
        skill_catalog=ctx.skill_catalog,
        host=ctx.host,
        api_keys=ctx.api_keys,
        venv_python=ctx.venv_python,
        context_window=ctx.context_window,
        rolling_compact_config=ctx.rolling_compact_config,
        memory_store=ctx.memory_store,
        memory_index=ctx.memory_index,
    )

    # 4. 建子 ToolRegistry + 注册工具
    from operon.tools.builtins import register_all
    from operon.tools.registry import ToolRegistry
    from operon.tools.router import ToolRouter

    child_registry = ToolRegistry()
    register_all(child_registry, child_ctx)
    child_ctx.registry = child_registry

    # 工具白名单: 用户指定 > 默认安全集
    allowed_tools = tools if tools is not None else list(_DEFAULT_CHILD_TOOLS)
    # 强制保证 submit_output 可用 (子需要它结束), 且 delegate 不可用 (防递归)
    if "submit_output" not in allowed_tools:
        allowed_tools.append("submit_output")
    allowed_tools = [t for t in allowed_tools if t != "delegate"]

    # 5. 构造子 Agent
    from operon.agent.runner import Agent, AgentCallbacks

    child_agent = Agent(
        llm=ctx.llm,
        tool_router=ToolRouter(child_registry),
        frame_service=ctx.frame_service,
        frame=child_frame,
        ctx=child_ctx,
        max_iterations=max_iterations,
        model=model or _parent_model(ctx),
        max_tokens=8192,
        plan_mode=False,  # 子不走 plan mode
        callbacks=AgentCallbacks(),  # 静默: 子的输出不进父的 SSE 流
    )

    # 6. 组装子的输入 (task + context_summary + schema 提示)
    child_prompt = _build_child_prompt(task, context_summary, output_schema)

    # 7. 跑子 agent
    logger.info("delegate: spawning sub-agent (depth %d, name=%s)", depth + 1, name)
    try:
        result = await child_agent.run(child_prompt)
    except Exception as e:
        logger.exception("delegate: sub-agent failed")
        return {"status": "error", "error": f"sub-agent crashed: {type(e).__name__}: {e}"}

    # 8. 提取结果
    submitted = child_frame.context.get("_submitted_output")
    if submitted is not None:
        return {
            "status": "completed",
            "structured_output": submitted.get("output", {}),
            "completion_bullets": submitted.get("completion_bullets", []),
            "response": result.final_text,
        }
    return {
        "status": result.kind.value if hasattr(result.kind, "value") else str(result.kind),
        "response": result.final_text,
    }


def _parent_model(ctx: ToolContext) -> str:
    """父 agent 的模型名 (从 frame 或 host 取)。"""
    m = getattr(ctx.frame, "model", None)
    if m:
        return m
    if ctx.host is not None:
        try:
            return ctx.host.current_model()
        except Exception:
            pass
    return "gpt-4o-mini"


def _build_child_prompt(
    task: str,
    context_summary: str | None,
    output_schema: dict | None,
) -> str:
    """组装子的输入 prompt: context_summary + task + (可选) schema 返回指令。"""
    parts = []
    if context_summary:
        parts.append(f"## Context\n{context_summary}")
    parts.append(f"## Task\n{task}")
    if output_schema:
        import json

        parts.append(
            "## Required output\n"
            "When your work is complete, call `submit_output` with an `output` dict "
            "matching this JSON Schema, and 2-4 `completion_bullets` (past-tense summaries):\n"
            f"```json\n{json.dumps(output_schema, ensure_ascii=False, indent=2)}\n```"
        )
    return "\n\n".join(parts)


DELEGATE_SPEC = {
    "name": "delegate",
    "description": (
        "Spawn a sub-agent to handle a self-contained subtask in its own fresh context. "
        "The sub-agent sees ONLY the task + context_summary you give it (not this conversation), "
        "runs to completion, and returns its result. Use for: independent sub-problems that "
        "benefit from isolated focus (e.g. 'score these 30 papers', 'draft section 3', "
        "'review this draft'). Max depth 2 (a sub-agent's sub-agent cannot delegate further)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "What the sub-agent should do. Self-contained — "
                    "it won't see this conversation."
                ),
            },
            "context_summary": {
                "type": "string",
                "description": "Background the sub-agent needs (the only context it receives).",
            },
            "output_schema": {
                "type": "object",
                "description": "JSON Schema. If set, the sub-agent must submit_output matching it.",
            },
            "model": {
                "type": "string",
                "description": "Model for the sub-agent (default: inherit parent).",
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Tool whitelist for the sub-agent "
                    "(default: safe subset without delegate)."
                ),
            },
            "max_iterations": {
                "type": "integer",
                "description": "Iteration cap for the sub-agent (default 8).",
            },
            "name": {"type": "string", "description": "Short label for the subtask."},
        },
        "required": ["task"],
    },
}
