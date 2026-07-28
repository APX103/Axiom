"""验证 harness 测试。

对照原版 0850.js oL_。
用 mock LLM 模拟 reviewer 返回 findings,验证:
1. checkpoint 触发阈值
2. findings 解析 + verdict 过滤
3. verification_checks 持久化
4. 通知队列 + drain
5. terminal barrier + bounce
"""

from __future__ import annotations

import pytest

from axiom_core.frames.service import FrameService
from axiom_core.llm.base import LLMClient
from axiom_core.llm.messages import (
    LLMResponse,
    Message,
    Role,
    StopReason,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)
from axiom_core.verify.dispositions import Verdict
from axiom_core.verify.verifier import VerificationConfig, Verifier


class MockReviewerLLM(LLMClient):
    """模拟 reviewer 的 LLM: 返回 submit_output 工具调用。"""

    def __init__(self, findings: list[dict]):
        self.findings = findings
        self.calls = 0

    async def chat(
        self, messages, *, system=None, tools=None, model=None, max_tokens=8192,
        temperature=None, **kw
    ):
        self.calls += 1
        return LLMResponse(
            content=[
                ToolUseBlock(
                    id="review_1",
                    name="submit_output",
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


@pytest.fixture
def frame_with_work():
    """造一个有工具产出的 frame (触发 checkpoint)。"""
    svc = FrameService()
    f = svc.create_root_frame()
    # 模拟 agent 写了 3 个 artifact (tool_result 含 version_id)
    f.messages.extend(
        [
            Message(role=Role.USER, content="写3个文件"),
            Message(
                role=Role.ASSISTANT,
                content=[
                    ToolUseBlock(
                        id="t1", name="write_file", input={"path": "a.txt", "content": "x"}
                    )
                ],
            ),
            Message(
                role=Role.USER,
                content=[
                    ToolResultBlock(
                        tool_use_id="t1",
                        content='{"version_id": "v1"}',
                    )
                ],
            ),
            Message(
                role=Role.ASSISTANT,
                content=[
                    ToolUseBlock(
                        id="t2", name="write_file", input={"path": "b.txt", "content": "y"}
                    )
                ],
            ),
            Message(
                role=Role.USER,
                content=[ToolResultBlock(tool_use_id="t2", content='{"version_id": "v2"}')],
            ),
            Message(
                role=Role.ASSISTANT,
                content=[
                    ToolUseBlock(
                        id="t3", name="write_file", input={"path": "c.txt", "content": "z"}
                    )
                ],
            ),
            Message(
                role=Role.USER,
                content=[ToolResultBlock(tool_use_id="t3", content='{"version_id": "v3"}')],
            ),
        ]
    )
    return svc, f


# ---------- 触发阈值 ----------


def test_checkpoint_triggers_on_artifact_delta(frame_with_work):
    """写了 3 个 artifact >= min_artifact_delta(3) 应触发 (时间够)。"""
    svc, f = frame_with_work
    cfg = VerificationConfig(enabled=True, min_artifact_delta=3, min_checkpoint_interval_ms=0)
    v = Verifier(llm=MockReviewerLLM([]), frame_service=svc, frame=f, config=cfg)
    assert v.maybe_checkpoint() is True


def test_checkpoint_no_trigger_when_disabled(frame_with_work):
    """disabled 时不触发。"""
    svc, f = frame_with_work
    cfg = VerificationConfig(enabled=False)
    v = Verifier(llm=MockReviewerLLM([]), frame_service=svc, frame=f, config=cfg)
    assert v.maybe_checkpoint() is False


def test_checkpoint_no_trigger_below_delta():
    """artifact 不足不触发。"""
    svc = FrameService()
    f = svc.create_root_frame()
    f.messages.append(Message(role=Role.USER, content="hi"))
    cfg = VerificationConfig(enabled=True, min_artifact_delta=3, min_checkpoint_interval_ms=0)
    v = Verifier(llm=MockReviewerLLM([]), frame_service=svc, frame=f, config=cfg)
    assert v.maybe_checkpoint() is False


# ---------- findings 解析 + 持久化 ----------


@pytest.mark.asyncio
async def test_checkpoint_persists_findings(frame_with_work):
    """checkpoint 应调 reviewer + 持久化 findings。"""
    svc, f = frame_with_work
    findings_data = [
        {"msg_idx": 1, "claim": "claim A", "verdict": "pass", "evidence": "traced"},
        {"msg_idx": 2, "claim": "claim B", "verdict": "fail", "evidence": "fabricated"},
        {"msg_idx": 3, "claim": "claim C", "verdict": "warn", "evidence": "label mismatch"},
    ]
    llm = MockReviewerLLM(findings_data)
    cfg = VerificationConfig(enabled=True, min_artifact_delta=3, min_checkpoint_interval_ms=0)
    v = Verifier(llm=llm, frame_service=svc, frame=f, config=cfg)
    result = await v.checkpoint(terminal=True)
    assert len(result) == 3
    assert llm.calls == 1
    # 持久化到 checks
    assert len(v.checks) == 3
    assert any(c.verdict == "fail" for c in v.checks)


# ---------- 通知队列 ----------


@pytest.mark.asyncio
async def test_notice_only_fail_warn_queued(frame_with_work):
    """只有 fail/warn findings 入队 (对照 queueNoticeFor 过滤)。"""
    svc, f = frame_with_work
    findings_data = [
        {"msg_idx": 1, "claim": "ok", "verdict": "pass", "evidence": "fine"},
        {"msg_idx": 2, "claim": "bad", "verdict": "fail", "evidence": "wrong"},
    ]
    llm = MockReviewerLLM(findings_data)
    cfg = VerificationConfig(enabled=True, min_artifact_delta=3, min_checkpoint_interval_ms=0)
    v = Verifier(llm=llm, frame_service=svc, frame=f, config=cfg)
    await v.checkpoint(terminal=True)
    notice = v.drain_pending_notices()
    assert notice is not None
    # 只有 fail 入队 (pass 被过滤)
    assert len(notice["findings"]) == 1
    assert notice["findings"][0].verdict == Verdict.FAIL
    assert "bad" in notice["text"]


@pytest.mark.asyncio
async def test_notice_empty_when_all_pass(frame_with_work):
    """全 pass 时无通知。"""
    svc, f = frame_with_work
    findings_data = [{"msg_idx": 0, "claim": "ok", "verdict": "pass", "evidence": "fine"}]
    llm = MockReviewerLLM(findings_data)
    cfg = VerificationConfig(enabled=True, min_artifact_delta=3, min_checkpoint_interval_ms=0)
    v = Verifier(llm=llm, frame_service=svc, frame=f, config=cfg)
    await v.checkpoint(terminal=True)
    assert v.drain_pending_notices() is None


# ---------- terminal barrier + bounce ----------


@pytest.mark.asyncio
async def test_terminal_barrier_veto_on_fail(frame_with_work):
    """有 fail findings → terminal barrier veto (阻止完成,要求修复)。"""
    svc, f = frame_with_work
    findings_data = [{"msg_idx": 1, "claim": "错", "verdict": "fail", "evidence": "e"}]
    llm = MockReviewerLLM(findings_data)
    cfg = VerificationConfig(enabled=True, min_artifact_delta=0, min_checkpoint_interval_ms=0)
    v = Verifier(llm=llm, frame_service=svc, frame=f, config=cfg)
    barrier = await v.terminal_barrier()
    assert barrier["veto"] is True
    assert barrier["notice"] is not None


@pytest.mark.asyncio
async def test_terminal_barrier_no_veto_on_pass(frame_with_work):
    """全 pass → 不 veto。"""
    svc, f = frame_with_work
    findings_data = [{"msg_idx": 0, "claim": "ok", "verdict": "pass", "evidence": "e"}]
    llm = MockReviewerLLM(findings_data)
    cfg = VerificationConfig(enabled=True, min_artifact_delta=0, min_checkpoint_interval_ms=0)
    v = Verifier(llm=llm, frame_service=svc, frame=f, config=cfg)
    barrier = await v.terminal_barrier()
    assert barrier["veto"] is False


@pytest.mark.asyncio
async def test_bounce_cap_suppresses_after_max(frame_with_work):
    """达 max_consecutive_bounces 后不再 veto (抑制)。对照 max_consecutive_bounces。"""
    svc, f = frame_with_work
    findings_data = [{"msg_idx": 0, "claim": "错", "verdict": "fail", "evidence": "e"}]
    llm = MockReviewerLLM(findings_data)
    cfg = VerificationConfig(
        enabled=True, min_artifact_delta=0, min_checkpoint_interval_ms=0, max_consecutive_bounces=2
    )
    v = Verifier(llm=llm, frame_service=svc, frame=f, config=cfg)
    # 模拟已 bounce 2 次
    v.verification_bounces = 2
    barrier = await v.terminal_barrier()
    # 第 3 次达上限 → 不再 veto
    assert barrier["veto"] is False


# ---------- 通知文本格式 ----------


@pytest.mark.asyncio
async def test_notice_text_format(frame_with_work):
    """通知文本含 [Auditor] 和 findings 标记。对照 kjz。"""
    svc, f = frame_with_work
    findings_data = [
        {"msg_idx": 1, "claim": "伪造引用", "verdict": "fail", "evidence": "doi 不存在"}
    ]
    llm = MockReviewerLLM(findings_data)
    cfg = VerificationConfig(enabled=True, min_artifact_delta=3, min_checkpoint_interval_ms=0)
    v = Verifier(llm=llm, frame_service=svc, frame=f, config=cfg)
    await v.checkpoint(terminal=True)
    notice = v.drain_pending_notices()
    assert "[Auditor]" in notice["text"]
    assert "<verification_findings" in notice["text"]
    assert "[fail]" in notice["text"]
    assert "伪造引用" in notice["text"]
