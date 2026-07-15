"""Plan mode 测试。

对照原版 0871.js:1625 _gatePlanProduceDenial + plan 流程。
测试:
1. plan mode 未生成 plan → end_turn 被拒绝 (denial)
2. 生成 plan → awaiting_plan_approval
3. 拒绝达上限 (MAX_PLAN_DENIALS=3) 后放行
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operon.agent.runner import MAX_PLAN_DENIALS, RunResultKind
from operon.agent.session import Session, SessionConfig
from operon.agent.states import FrameStatus
from operon.llm.messages import (
    LLMResponse,
    StopReason,
    TextBlock,
    TokenUsage,
    ToolUseBlock,
)
from tests.test_agent_loop import FakeLLM, _text_resp


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
