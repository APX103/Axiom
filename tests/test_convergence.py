"""收敛机制测试 — plan 锚点 + reviewer 收敛审查。

验证三件事 (对照原版的 anti-drift 机制):
1. generate_plan 带 research_question 时, 锚点被正确存入 PlanState。
2. get_plan_summary 把 research_question 渲染进 prompt (每轮重新注入, 对抗遗忘)。
3. Verifier 收到带锚点的 plan 后:
   a. reviewer system prompt 含收敛审查指令
   b. reviewer prompt 含 "Original ask" 段 (research_question 作为标尺)
   c. terminal_barrier 在有 fail 收敛 finding 时返回 veto (逼 agent 回去修)
"""

from __future__ import annotations

import pytest

from operon.frames.service import FrameService
from operon.llm.base import LLMClient
from operon.llm.messages import LLMResponse, Message, Role, StopReason, TokenUsage, ToolUseBlock
from operon.tools.builtins import plan as plan_tools
from operon.tools.context import PlanState, ToolContext
from operon.verify.verifier import VerificationConfig, Verifier

# ---------- 1. plan 锚点存取 ----------


def _ctx_with_plan() -> ToolContext:
    svc = FrameService()
    f = svc.create_root_frame()
    ctx = ToolContext.__new__(ToolContext)
    ctx.frame = f
    ctx.frame_service = svc
    ctx.plan = PlanState()
    return ctx


@pytest.mark.asyncio
async def test_generate_plan_stores_research_question():
    """generate_plan 带 research_question/scope/desired_outputs 时正确存入 PlanState。"""
    ctx = _ctx_with_plan()
    await plan_tools.generate_plan(
        ctx,
        steps=[{"id": "s1", "description": "检索文献"}, {"id": "s2", "description": "撰写综述"}],
        research_question="哪些方法能提升 LLM 推理能力,它们如何对比?",
        scope="仅限 prompting 与训练方法,不含硬件",
        desired_outputs=["LaTeX survey paper", "references.bib"],
        feasibility={"confidence": "high", "rationale": "文献充足"},
    )
    assert ctx.plan.research_question == "哪些方法能提升 LLM 推理能力,它们如何对比?"
    assert ctx.plan.scope == "仅限 prompting 与训练方法,不含硬件"
    assert ctx.plan.desired_outputs == ["LaTeX survey paper", "references.bib"]
    assert ctx.plan.feasibility == {"confidence": "high", "rationale": "文献充足"}


@pytest.mark.asyncio
async def test_generate_plan_without_anchor_stays_none():
    """不带锚点的普通 plan, 新字段保持默认 (向后兼容)。"""
    ctx = _ctx_with_plan()
    await plan_tools.generate_plan(ctx, steps=[{"id": "s1", "description": "查一下"}])
    assert ctx.plan.research_question is None
    assert ctx.plan.desired_outputs == []


# ---------- 2. plan summary 注入锚点 ----------


def test_plan_summary_includes_research_question():
    """get_plan_summary 把 research_question 渲染进 prompt, 对抗长对话遗忘。"""
    ctx = _ctx_with_plan()
    ctx.plan.steps = [{"id": "s1", "description": "x", "status": "pending"}]
    ctx.plan.research_question = "RQ: 如何量化推理能力?"
    summary = plan_tools.get_plan_summary(ctx)
    assert "Research Question" in summary
    assert "如何量化推理能力?" in summary


def test_plan_summary_skips_anchor_when_absent():
    """无 research_question 时 summary 不画蛇添足 (普通 plan 不受影响)。"""
    ctx = _ctx_with_plan()
    ctx.plan.steps = [{"id": "s1", "description": "x", "status": "pending"}]
    summary = plan_tools.get_plan_summary(ctx)
    assert "Research Question" not in summary


# ---------- 3. Verifier 收敛审查 ----------


class _MockReviewerLLM(LLMClient):
    """模拟 reviewer: 返回给定的 findings。"""

    def __init__(self, findings: list[dict]):
        self.findings = findings

    async def chat(
        self, messages, *, system=None, tools=None, model=None,
        max_tokens=8196, temperature=None, **kw,
    ):
        return LLMResponse(
            content=[
                ToolUseBlock(
                    id="r1", name="submit_output",
                    input={"findings": self.findings},
                )
            ],
            stop_reason=StopReason.TOOL_USE,
            model="mock-reviewer",
            usage=TokenUsage(input_tokens=100, output_tokens=50),
        )

    def count_tokens(self, text):
        return len(text) // 4

    async def close(self):
        pass


def _verifier_with_plan(research_question: str | None, findings: list[dict]) -> Verifier:
    """构造带 plan 锚点的 Verifier。"""
    svc = FrameService()
    f = svc.create_root_frame()
    f.messages.extend([
        Message(role=Role.USER, content="写一篇综述"),
        Message(role=Role.ASSISTANT, content="[drafted sections...]"),
    ])
    plan = PlanState(
        research_question=research_question,
        scope="LLM reasoning" if research_question else None,
    )
    llm = _MockReviewerLLM(findings)
    return Verifier(
        llm=llm,
        frame_service=svc,
        frame=f,
        config=VerificationConfig(enabled=True),
        reviewer_model="mock-reviewer",
        plan=plan,
    )


def test_reviewer_prompt_contains_original_ask_when_anchor_present():
    """plan 带 research_question 时, reviewer prompt 含 'Original ask' 段 (标尺)。"""
    v = _verifier_with_plan("RQ: 如何提升推理?", [])
    prompt = v._build_review_prompt(0, len(v.frame.messages), terminal=False)
    assert "Original ask" in prompt
    assert "如何提升推理?" in prompt


def test_reviewer_prompt_omits_original_ask_when_no_anchor():
    """无锚点时 reviewer prompt 不含收敛段 (普通审查不受影响)。"""
    v = _verifier_with_plan(None, [])
    prompt = v._build_review_prompt(0, len(v.frame.messages), terminal=False)
    assert "Original ask" not in prompt


@pytest.mark.asyncio
async def test_terminal_barrier_vetoes_on_fail_finding():
    """有 fail 级收敛 finding 时, terminal_barrier 返回 veto=True (逼 agent 回去修)。"""
    fail_finding = [
        {
            "msg_idx": 1,
            "claim": "Section 4 drifts from research question: 该节讨论硬件, 超出 scope",
            "verdict": "fail",
            "evidence": "scope 限定为 prompting/训练方法",
        }
    ]
    v = _verifier_with_plan("RQ: 如何提升推理?", fail_finding)
    result = await v.terminal_barrier()
    assert result["veto"] is True, "fail 收敛 finding 应触发 veto 逼 agent 回去修"
    assert result["notice"] is not None
    # notice 文本里要能看出是哪节偏离
    assert "drifts" in result["notice"]["text"] or "Section 4" in result["notice"]["text"]


@pytest.mark.asyncio
async def test_terminal_barrier_passes_when_clean():
    """无 finding 时 terminal_barrier 不 veto (正常通过)。"""
    v = _verifier_with_plan("RQ: 如何提升推理?", [])
    result = await v.terminal_barrier()
    assert result["veto"] is False
    assert result["notice"] is None
