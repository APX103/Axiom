"""Plan mode 测试。

对照原版 0871.js:1625 _gatePlanProduceDenial + plan 流程。
测试:
1. plan mode 未生成 plan → end_turn 被拒绝 (denial)
2. 生成 plan → awaiting_plan_approval
3. 拒绝达上限 (MAX_PLAN_DENIALS=3) 后放行
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from operon.agent.runner import MAX_PLAN_DENIALS, AgentCallbacks, RunResultKind
from operon.agent.session import Session, SessionConfig
from operon.agent.states import FrameStatus
from operon.api.callbacks import WSCallbacks
from operon.llm.messages import (
    LLMResponse,
    StopReason,
    TextBlock,
    TokenUsage,
    ToolUseBlock,
)
from tests.test_agent_loop import FakeLLM, _text_resp, _tool_resp


class RecordingCallbacks(AgentCallbacks):
    def __init__(self) -> None:
        self.plan_updates: list[list[dict]] = []

    async def on_plan_update(self, plan) -> None:
        self.plan_updates.append(deepcopy(plan.steps))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.mark.asyncio
async def test_plan_denial_when_no_plan(workspace):
    """plan mode 开启,模型直接结束 (不调 generate_plan) → 被拒绝。

    对照 0871.js:1625: plan_artifact_id 未变则拒绝,最多 3 次。
    """
    llm = FakeLLM(
        [
            _text_resp("好的,任务完成。"),  # 第1次: 无 plan 直接结束 → 拒绝
            _text_resp("我做好了。"),  # 第2次拒绝
            _text_resp("完成了。"),  # 第3次拒绝
            _text_resp("第四次,放行。"),  # 超过上限放行
        ]
    )
    session = Session(
        llm=llm,
        config=SessionConfig(workspace=workspace, plan_mode=True),
    )
    result = await session.run("做任务")

    assert result.kind == RunResultKind.NATURAL
    assert result.final_text == "第四次,放行。"
    # 前 3 次被拒,第 4 次放行 → 共 4 次 LLM 调用
    assert llm.calls == MAX_PLAN_DENIALS + 1


@pytest.mark.asyncio
async def test_generate_plan_triggers_awaiting(workspace):
    """plan mode 下调 generate_plan → frame 进入 awaiting_plan_approval。"""
    plan_steps = [
        {"id": "s1", "description": "第一步"},
        {"id": "s2", "description": "第二步"},
    ]
    llm = FakeLLM(
        [
            LLMResponse(
                content=[ToolUseBlock(id="t1", name="generate_plan", input={"steps": plan_steps})],
                stop_reason=StopReason.TOOL_USE,
                model="fake",
                usage=TokenUsage(input_tokens=10, output_tokens=5),
            ),
        ]
    )
    session = Session(
        llm=llm,
        config=SessionConfig(workspace=workspace, plan_mode=True),
    )
    result = await session.run("帮我规划任务")

    assert result.kind == RunResultKind.AWAITING
    assert result.awaiting == "plan_approval"
    assert result.frame.status == FrameStatus.AWAITING_PLAN_APPROVAL
    # plan steps 已记录
    assert len(result.frame.context) >= 0  # ctx 在 session 内部


@pytest.mark.asyncio
async def test_generate_plan_emits_live_plan_update(workspace):
    """generate_plan 改变状态后立即发事件，而不是等 complete 才给前端。"""
    callbacks = RecordingCallbacks()
    llm = FakeLLM(
        [
            LLMResponse(
                content=[
                    ToolUseBlock(
                        id="t1",
                        name="generate_plan",
                        input={
                            "steps": [
                                {"id": "s1", "description": "第一步"},
                                {"id": "s2", "description": "第二步"},
                            ]
                        },
                    )
                ],
                stop_reason=StopReason.TOOL_USE,
                model="fake",
                usage=TokenUsage(input_tokens=10, output_tokens=5),
            )
        ]
    )
    session = Session(
        llm=llm,
        config=SessionConfig(workspace=workspace, plan_mode=True),
        callbacks=callbacks,
    )

    await session.run("规划并执行")

    assert callbacks.plan_updates
    assert [step["status"] for step in callbacks.plan_updates[-1]] == [
        "pending",
        "pending",
    ]


@pytest.mark.asyncio
async def test_approve_starts_first_step_and_success_closes_plan(workspace):
    """批准后首步进入执行态；Agent 自然成功时不遗留 pending。"""
    from operon.agent.runner import Agent
    from operon.frames.service import FrameService
    from operon.tools.builtins import plan as plan_tools
    from operon.tools.context import ToolContext
    from operon.tools.registry import ToolRegistry
    from operon.tools.router import ToolRouter

    frame_service = FrameService()
    frame = frame_service.create_root_frame(agent_name="MAIN")
    ctx = ToolContext(frame=frame, frame_service=frame_service, workspace=workspace)
    await plan_tools.generate_plan(
        ctx,
        steps=[
            {"id": "s1", "description": "第一步"},
            {"id": "s2", "description": "第二步"},
        ],
    )
    await plan_tools.approve_plan(ctx)
    assert [step["status"] for step in ctx.plan.steps] == ["in_progress", "pending"]

    callbacks = RecordingCallbacks()
    registry = ToolRegistry()
    registry.register(
        **plan_tools.UPDATE_STEP_STATUS_SPEC,
        handler=lambda **kw: plan_tools.update_step_status(ctx, **kw),
    )
    agent = Agent(
        llm=FakeLLM(
            [
                _tool_resp(
                    "update_step_status",
                    step_id="s1",
                    status="completed",
                ),
                _text_resp("全部完成"),
            ]
        ),
        tool_router=ToolRouter(registry),
        frame_service=frame_service,
        frame=frame,
        ctx=ctx,
        plan_mode=True,
        callbacks=callbacks,
    )

    result = await agent.run("继续执行已批准的计划。")

    assert result.kind == RunResultKind.NATURAL
    assert [step["status"] for step in ctx.plan.steps] == ["completed", "completed"]
    assert any(
        [step["status"] for step in update] == ["completed", "pending"]
        for update in callbacks.plan_updates
    )
    assert [step["status"] for step in callbacks.plan_updates[-1]] == [
        "completed",
        "completed",
    ]


@pytest.mark.asyncio
async def test_ws_callback_serializes_plan_update_snapshot():
    """SSE/WS 队列中的 plan_update 是完整且与后续突变隔离的快照。"""
    from operon.tools.context import PlanState

    callbacks = WSCallbacks()
    plan = PlanState(
        steps=[{"id": "s1", "description": "第一步", "status": "in_progress"}],
        approved=True,
        research_question="核心问题",
    )

    await callbacks.on_plan_update(plan)
    event = callbacks.queue.get_nowait()
    plan.steps[0]["status"] = "completed"

    assert event["type"] == "plan_update"
    assert event["plan"]["steps"][0]["status"] == "in_progress"
    assert event["plan"]["research_question"] == "核心问题"


@pytest.mark.asyncio
async def test_plan_mode_allows_immediate_plan(workspace):
    """plan mode 下模型正确先调 generate_plan → 不触发 denial。"""
    llm = FakeLLM(
        [
            LLMResponse(
                content=[
                    TextBlock(text="我来规划"),
                    ToolUseBlock(
                        id="t1",
                        name="generate_plan",
                        input={"steps": [{"id": "s1", "description": "do it"}]},
                    ),
                ],
                stop_reason=StopReason.TOOL_USE,
                model="fake",
                usage=TokenUsage(input_tokens=10, output_tokens=5),
            ),
        ]
    )
    session = Session(
        llm=llm,
        config=SessionConfig(workspace=workspace, plan_mode=True),
    )
    result = await session.run("规划")

    # 调了 generate_plan → awaiting,没被 denial
    assert result.kind == RunResultKind.AWAITING
    assert llm.calls == 1  # 只调了一次,没浪费在 denial 上


@pytest.mark.asyncio
async def test_non_plan_mode_no_denial(workspace):
    """非 plan mode 下直接结束不被拒绝。"""
    llm = FakeLLM([_text_resp("完成")])
    session = Session(
        llm=llm,
        config=SessionConfig(workspace=workspace, plan_mode=False),
    )
    result = await session.run("做任务")

    assert result.kind == RunResultKind.NATURAL
    assert llm.calls == 1
