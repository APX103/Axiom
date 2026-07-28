"""终态 frame 继续对话 (reopen) 的回归测试。

之前: 已 completed/failed/cancelled 的会话再发消息会报
"frame is already terminal (completed); start a new session"。
现在: Agent.run 通过 FrameService.reopen 重新打开 frame, 直接继续对话。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiom_core.agent.runner import Agent, RunResultKind
from axiom_core.agent.session import Session, SessionConfig
from axiom_core.agent.states import TERMINAL, FrameStatus
from axiom_core.frames.service import FrameService
from axiom_core.tools.router import ToolRouter


def test_frame_service_reopen_resets_terminal():
    """update_status 禁止终态变更; reopen 是唯一合法出口。"""
    svc = FrameService()
    f = svc.create_root_frame()
    svc.update_status(f.id, FrameStatus.COMPLETED)
    assert f.completed_at is not None

    with pytest.raises(ValueError):
        svc.update_status(f.id, FrameStatus.PROCESSING)

    svc.reopen(f.id)
    assert f.status == FrameStatus.PROCESSING
    assert f.completed_at is None

    # 非终态调 reopen 是 no-op
    svc.reopen(f.id)
    assert f.status == FrameStatus.PROCESSING


def test_frame_service_reopen_all_terminal_states():
    """每个终态都能被 reopen。"""
    svc = FrameService()
    for status in TERMINAL:
        f = svc.create_root_frame()
        f.status = status
        svc.reopen(f.id)
        assert f.status == FrameStatus.PROCESSING


@pytest.mark.asyncio
async def test_agent_run_continues_on_completed_frame(
    workspace: Path, fake_llm_factory, text_resp
):
    """同一 frame 完成一轮后再 run 不再报 "frame is already terminal"。"""
    llm = fake_llm_factory([text_resp("第一轮回答"), text_resp("第二轮回答")])
    session = Session(llm=llm, config=SessionConfig(workspace=workspace))
    ctx = await session.prepare()
    frame = ctx.frame
    router = ToolRouter(session.registry)

    def make_agent() -> Agent:
        return Agent(
            llm=llm,
            tool_router=router,
            frame_service=session.frame_service,
            frame=frame,
            ctx=ctx,
            max_iterations=5,
            model=None,
            max_tokens=8192,
            plan_mode=False,
            deep_review=False,
        )

    r1 = await make_agent().run("第一个问题")
    assert r1.kind != RunResultKind.ERROR
    assert frame.status in TERMINAL  # 第一轮结束后进入终态

    # 修复前: 这里直接返回 ERROR "frame is already terminal (completed)"
    r2 = await make_agent().run("继续问")
    assert r2.kind != RunResultKind.ERROR
    assert llm.calls == 2
    # 对话历史保留: 两轮的用户消息都在
    user_texts = [m.content for m in frame.messages if m.role.value == "user"]
    assert "第一个问题" in user_texts
    assert "继续问" in user_texts
