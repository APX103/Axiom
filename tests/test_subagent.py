"""子 agent (delegate) 测试。

验证 delegate 工具的核心行为:
1. 父 agent 调 delegate → 子 agent 在独立 frame 跑完 → 结果回传
2. 上下文隔离: 子的 frame.messages 不含父对话
3. submit_output 结构化返回
4. 防递归: 深度超限返回错误
5. 子的工具集不含 delegate
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiom_core.agent.runner import Agent, AgentCallbacks
from axiom_core.frames.service import FrameService
from axiom_core.llm.base import LLMClient
from axiom_core.llm.messages import (
    LLMResponse,
    Message,
    Role,
    StopReason,
    TextBlock,
    TokenUsage,
    ToolUseBlock,
)
from axiom_core.tools.builtins import register_all
from axiom_core.tools.context import ToolContext
from axiom_core.tools.registry import ToolRegistry
from axiom_core.tools.router import ToolRouter


class FakeLLM(LLMClient):
    """按脚本返回响应。每次 chat 弹一个。"""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls = 0

    async def chat(
        self, messages, *, system=None, tools=None, model=None,
        max_tokens=8192, temperature=None, **kw,
    ):
        self.calls += 1
        if not self.responses:
            raise AssertionError("FakeLLM ran out of scripted responses")
        return self.responses.pop(0)

    def count_tokens(self, text):
        return len(text) // 4

    async def close(self):
        pass


def _text_resp(text: str) -> LLMResponse:
    return LLMResponse(
        content=[TextBlock(text=text)],
        stop_reason=StopReason.END_TURN,
        model="fake",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


def _tool_resp(name: str, tool_id: str = "t1", **inputs) -> LLMResponse:
    return LLMResponse(
        content=[ToolUseBlock(id=tool_id, name=name, input=inputs)],
        stop_reason=StopReason.TOOL_USE,
        model="fake",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


def _make_ctx(workspace: Path, llm: FakeLLM) -> tuple[ToolContext, ToolRegistry]:
    """构造一个带完整工具的父 ctx + registry。"""
    svc = FrameService()
    frame = svc.create_root_frame()
    ctx = ToolContext(
        frame=frame, frame_service=svc, workspace=workspace,
        llm=llm, context_window=100000,
    )
    registry = ToolRegistry()
    register_all(registry, ctx)
    ctx.registry = registry
    return ctx, registry


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


# ---------- 1. delegate 基本链路: 子返回文本 ----------


@pytest.mark.asyncio
async def test_delegate_returns_text_result(workspace: Path):
    """父调 delegate, 子跑完返回文本, 父拿到结果。"""
    # FakeLLM 脚本:
    #   call 1 (父): 调 delegate 工具
    #   call 2 (子): 子直接返回文本 (无工具调用, 自然完成)
    #   call 3 (父): 拿到 delegate 结果后, 输出最终回复
    llm = FakeLLM([
        _tool_resp("delegate", "t1", task="算一下 1+1"),
        _text_resp("结果是 2"),
        _text_resp("子任务完成,答案是 2"),
    ])
    ctx, registry = _make_ctx(workspace, llm)
    agent = Agent(
        llm=llm, tool_router=ToolRouter(registry), frame_service=ctx.frame_service,
        frame=ctx.frame, ctx=ctx, model="fake", callbacks=AgentCallbacks(),
    )
    result = await agent.run("帮我算 1+1, 用 delegate 派给子 agent")

    # 父拿到子的结果并体现在最终输出
    assert "2" in result.final_text
    assert result.kind.value != "error"


# ---------- 2. 上下文隔离 ----------


@pytest.mark.asyncio
async def test_delegate_context_isolation(workspace: Path):
    """子的 frame.messages 不含父的对话历史。"""
    llm = FakeLLM([
        _tool_resp("delegate", "t1", task="独立子任务"),
        _text_resp("子完成了"),
        _text_resp("ok"),
    ])
    ctx, registry = _make_ctx(workspace, llm)
    # 给父 frame 塞一段"机密"对话
    ctx.frame.messages.append(Message(role=Role.USER, content="这是父的机密对话,子不该看到"))

    agent = Agent(
        llm=llm, tool_router=ToolRouter(registry), frame_service=ctx.frame_service,
        frame=ctx.frame, ctx=ctx, model="fake", callbacks=AgentCallbacks(),
    )
    await agent.run("派活")

    # 找到子 frame
    children = [f for f in ctx.frame_service._frames.values() if f.parent_frame_id == ctx.frame.id]
    assert len(children) >= 1
    child = children[0]
    # 子的 messages 里不应含父的机密对话
    child_texts = [
        b.text for m in child.messages if isinstance(m.content, list)
        for b in m.content if isinstance(b, TextBlock)
    ]
    # 子只应收到 delegate 组装的 task prompt
    assert all("机密对话" not in t for t in child_texts), \
        f"子 agent 看到了父的机密对话! {child_texts}"


# ---------- 3. submit_output 结构化返回 ----------


@pytest.mark.asyncio
async def test_delegate_structured_output(workspace: Path):
    """带 output_schema 时, 子调 submit_output, 父拿到 structured_output。"""
    llm = FakeLLM([
        _tool_resp(
            "delegate", "t1",
            task="评分这些论文",
            output_schema={"type": "object", "properties": {"score": {"type": "number"}}},
        ),
        # 子: 调 submit_output 提交结构化结果
        _tool_resp("submit_output", "s1", output={"score": 8.5}, completion_bullets=["评完了"]),
        # 父: 拿到结果后回复
        _text_resp("子 agent 评分 8.5"),
    ])
    ctx, registry = _make_ctx(workspace, llm)
    agent = Agent(
        llm=llm, tool_router=ToolRouter(registry), frame_service=ctx.frame_service,
        frame=ctx.frame, ctx=ctx, model="fake", callbacks=AgentCallbacks(),
    )
    await agent.run("评分")

    # 子 frame 的 context 应有 submitted_output
    children = [f for f in ctx.frame_service._frames.values() if f.parent_frame_id == ctx.frame.id]
    child = children[0]
    submitted = child.context.get("_submitted_output")
    assert submitted is not None
    assert submitted["output"]["score"] == 8.5


# ---------- 4. 防递归 ----------


@pytest.mark.asyncio
async def test_delegate_depth_limit(workspace: Path):
    """深度超限时 delegate 返回错误 (不无限递归)。"""
    # 手动构造一个深度=2 的 frame (root → child → grandchild), 在 grandchild 上调 delegate 应失败
    svc = FrameService()
    root = svc.create_root_frame()
    child = svc.create_child_frame(root.id, agent_name="SUBAGENT")
    grandchild = svc.create_child_frame(child.id, agent_name="SUBAGENT")

    llm = FakeLLM([])
    ctx = ToolContext(
        frame=grandchild, frame_service=svc, workspace=workspace,
        llm=llm, context_window=100000,
    )
    registry = ToolRegistry()
    register_all(registry, ctx)
    ctx.registry = registry

    from axiom_core.tools.builtins.delegate import delegate

    result = await delegate(ctx, task="再套一层")
    assert result["status"] == "error"
    assert "depth" in result["error"].lower()


# ---------- 5. 子工具集不含 delegate ----------


def test_child_tool_whitelist_excludes_delegate(workspace: Path):
    """默认子工具白名单不含 delegate (防递归)。"""
    from axiom_core.tools.builtins.delegate import _DEFAULT_CHILD_TOOLS

    assert "delegate" not in _DEFAULT_CHILD_TOOLS
    assert "submit_output" in _DEFAULT_CHILD_TOOLS
    # 不应有 ask_user / generate_plan (子不与用户交互)
    assert "ask_user" not in _DEFAULT_CHILD_TOOLS
    assert "generate_plan" not in _DEFAULT_CHILD_TOOLS


# ---------- 6. 子 frame 是 hidden ----------


@pytest.mark.asyncio
async def test_child_frame_is_hidden(workspace: Path):
    """子 frame 标记 is_hidden (不进 UI 视图)。"""
    llm = FakeLLM([
        _tool_resp("delegate", "t1", task="子任务"),
        _text_resp("完成"),
        _text_resp("ok"),
    ])
    ctx, registry = _make_ctx(workspace, llm)
    agent = Agent(
        llm=llm, tool_router=ToolRouter(registry), frame_service=ctx.frame_service,
        frame=ctx.frame, ctx=ctx, model="fake", callbacks=AgentCallbacks(),
    )
    await agent.run("派活")

    children = [f for f in ctx.frame_service._frames.values() if f.parent_frame_id == ctx.frame.id]
    assert len(children) == 1
    assert children[0].is_hidden is True
    assert children[0].agent_name == "SUBAGENT"
