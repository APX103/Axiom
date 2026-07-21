"""ask_user 工具 + 恢复流程测试。

回归: agent 调 ask_user 后 frame 进入 AWAITING_USER_RESPONSE, run() 返回 awaiting。
用户带回答再次 run() 时, 必须清掉 AWAITING 状态回到 PROCESSING, 否则循环顶部哨兵
立即 return, 回答永远不被处理 (用户卡死无法继续)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operon.agent.runner import RunResultKind
from operon.agent.states import FrameStatus
from operon.llm.base import LLMClient
from operon.llm.messages import (
    LLMResponse,
    StopReason,
    TextBlock,
    TokenUsage,
    ToolUseBlock,
)


class FakeLLM(LLMClient):
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls = 0

    async def chat(
        self, messages, *, system=None, tools=None, model=None,
        max_tokens=8192, temperature=None, **kw
    ):
        self.calls += 1
        if not self.responses:
            raise AssertionError("FakeLLM ran out of scripted responses")
        return self.responses.pop(0)

    def count_tokens(self, text):
        return len(text) // 4

    async def close(self):
        pass


def _ask_user_resp(question: str, options: list[str]) -> LLMResponse:
    return LLMResponse(
        content=[
            ToolUseBlock(id="t1", name="ask_user", input={"question": question, "options": options})
        ],
        stop_reason=StopReason.TOOL_USE,
        model="fake",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


def _text_resp(text: str) -> LLMResponse:
    return LLMResponse(
        content=[TextBlock(text=text)],
        stop_reason=StopReason.END_TURN,
        model="fake",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.mark.asyncio
async def test_ask_user_then_resume(workspace):
    """ask_user 挂起 → 用户回答后恢复执行。

    用同一个 Agent 跨两轮 run (保持同一 ctx/frame), 验证:
    1. 第一轮 ask_user → AWAITING_USER_RESPONSE, pending_ask 被设置
    2. 第二轮用户回答 → 清掉 AWAITING 状态回到 PROCESSING, 回答被处理, 正常完成
    """
    from operon.agent.runner import Agent
    from operon.frames.service import FrameService
    from operon.tools.context import ToolContext
    from operon.tools.registry import ToolRegistry
    from operon.tools.router import ToolRouter

    llm = FakeLLM([
        _ask_user_resp("用哪种模型?", ["GPT", "Claude"]),
        _text_resp("好的,开始用 GPT。"),
    ])
    frame_service = FrameService()
    frame = frame_service.create_root_frame(agent_name="MAIN", model="fake")
    ctx = ToolContext(
        frame=frame,
        frame_service=frame_service,
        workspace=workspace.resolve(),
    )
    router_registry = ToolRegistry()
    # 注册默认工具 (含 ask_user)
    from operon.tools.builtins import register_all
    register_all(router_registry, ctx)
    router = ToolRouter(router_registry)

    agent = Agent(
        llm=llm,
        tool_router=router,
        frame_service=frame_service,
        frame=frame,
        ctx=ctx,
        max_iterations=10,
        model="fake",
    )

    # 第一轮: 触发 ask_user, 应 awaiting
    r1 = await agent.run("帮我写综述")
    assert r1.kind == RunResultKind.AWAITING
    assert r1.awaiting == "user_response"
    assert frame.status == FrameStatus.AWAITING_USER_RESPONSE
    # pending_ask 带上了问题和选项
    assert ctx.pending_ask is not None
    assert ctx.pending_ask.question == "用哪种模型?"
    assert ctx.pending_ask.options == ["GPT", "Claude"]

    # 第二轮: 用户回答 → 必须清掉 AWAITING, 回答被处理, 正常完成
    r2 = await agent.run("GPT")
    assert r2.kind == RunResultKind.NATURAL
    assert frame.status != FrameStatus.AWAITING_USER_RESPONSE
    # pending_ask 已清掉
    assert ctx.pending_ask is None
