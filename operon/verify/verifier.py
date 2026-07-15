"""Verifier — 验证 harness 核心。


负责: 阈值 checkpoint 触发 + spawn REVIEWER 子 frame + findings 持久化 + 通知回传 MAIN。

简化 (保留核心,去掉并发优化):
- 保留: checkpoint 触发判断 / 同步 spawn reviewer / findings 写 verification_checks / 通知队列
- 简化: 去掉 hold/coalesce (直接派发) / shadow reviewer (release 关闭) / bookmarker (默认关闭) / rewind
- 阈值计算: artifactsWrittenSince / proseCharsSince / authoredInputCharsSince / mdBlockSince

集成点:
- runner 每轮工具执行后调 maybe_checkpoint()
- runner 自然完成时调 terminal_barrier()
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from operon.frames.model import Frame
from operon.frames.service import FrameService
from operon.llm.base import LLMClient
from operon.llm.messages import Message, Role, TextBlock, ToolResultBlock, ToolUseBlock

from .dispositions import (
    FINDINGS_OUTPUT_SCHEMA,
    Finding,
    Verdict,
)


@dataclass
class VerificationConfig:
    """验证配置。对照原版 0039.js:223-242 [verification]。

    enabled 默认 False: 避免在不需要审稿的场景 (单元测试/简单对话) 自动触发。
    真模型场景或需要质量把关时显式开启 (原版 release 也按 tier/条件开启)。
    """

    enabled: bool = False
    reviewer_model: str | None = None  # None=用会话主模型
    reviewer_max_iterations: int = 20
    n_per_checkpoint: int = 1
    min_artifact_delta: int = 3
    min_authored_input_chars: int = 2000
    min_prose_chars: int = 0
    min_checkpoint_interval_ms: int = 120000  # 2 分钟
    structural_block_trigger: bool = True
    max_consecutive_bounces: int = 3


# 写 artifact 的工具集 (对照原版 mL_ = {save_artifacts, generate_plan})
_ARTIFACT_WRITER_TOOLS = {"save_artifacts", "generate_plan", "write_file"}


class Verifier:
    """验证 harness。

    用 LLMClient 直接做 reviewer 调用 (简化: 不 spawn 子 frame,直接调 LLM)。
    原版 spawn 子 frame 是为了独立上下文 + reviewer 模型; 本版简化为单独 LLM 调用。
    """

    def __init__(
        self,
        *,
        llm: LLMClient,
        frame_service: FrameService,
        frame: Frame,
        config: VerificationConfig | None = None,
        reviewer_model: str | None = None,
    ):
        self.llm = llm
        self.frame_service = frame_service
        self.frame = frame
        self.config = config or VerificationConfig()
        self.reviewer_model = reviewer_model or self.config.reviewer_model

        # 运行时状态 (对照原版 _detached + state)
        self.last_checkpoint_idx = 0
        self.last_checkpoint_at = 0.0
        self.pending_notices: list[dict[str, Any]] = []
        self.checks: list = []  # VerificationCheck 列表 (内存态)
        self.verification_bounces = 0
        self.reviewer_busy = False

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    # ===== §2 checkpoint 触发判断 =====

    def maybe_checkpoint(self) -> bool:
        """检查是否该触发 checkpoint。对照原版 0871.js:1463-1484。

        只判断是否满足触发条件 (不执行)。runner 调用方负责 await checkpoint()。
        返回是否满足触发条件。
        """
        if not self.enabled:
            return False
        s = self.config
        now = time.time() * 1000
        if now - self.last_checkpoint_at < s.min_checkpoint_interval_ms:
            return False
        v = self.last_checkpoint_idx
        if (
            self._artifacts_written_since(v) >= s.min_artifact_delta
            or (s.structural_block_trigger and self._md_block_since(v))
            or (
                s.min_authored_input_chars > 0
                and self._authored_input_chars_since(v) >= s.min_authored_input_chars
            )
            or (s.min_prose_chars > 0 and self._prose_chars_since(v) >= s.min_prose_chars)
        ):
            return True
        return False

    def _artifacts_written_since(self, idx: int) -> int:
        """自 idx 以来写入的 artifact 数。对照原版 artifactsWrittenSince (0850.js:1593)。"""
        import json

        count = 0
        for m in self.frame.messages[idx:]:
            if isinstance(m.content, list):
                for b in m.content:
                    if isinstance(b, ToolResultBlock) and not b.is_error:
                        c = b.content
                        text = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                        # 找 version_id 出现次数 (简化: save_artifacts 结果含 version_id)
                        if "version_id" in text:
                            count += text.count('"version_id"')
        return count

    def _md_block_since(self, idx: int) -> bool:
        """是否有 markdown 结构 (表格/代码块)。对照原版 mdBlockSince。"""
        for m in self.frame.messages[idx:]:
            if m.role != Role.ASSISTANT or not isinstance(m.content, list):
                continue
            for b in m.content:
                if isinstance(b, TextBlock):
                    t = b.text
                    if "|" in t and "---" in t:  # 表格
                        return True
                    if t.count("```") >= 2:  # 代码块
                        return True
        return False

    def _prose_chars_since(self, idx: int) -> int:
        """assistant 文本字符数。对照原版 proseCharsSince。"""
        total = 0
        for m in self.frame.messages[idx:]:
            if m.role == Role.ASSISTANT and isinstance(m.content, list):
                for b in m.content:
                    if isinstance(b, TextBlock):
                        total += len(b.text)
        return total

    def _authored_input_chars_since(self, idx: int) -> int:
        """agent 在 tool_use.input 里写的字符数。对照原版 authoredInputCharsSince。"""
        import json

        total = 0
        for m in self.frame.messages[idx:]:
            if m.role == Role.ASSISTANT and isinstance(m.content, list):
                for b in m.content:
                    if isinstance(b, ToolUseBlock):
                        total += len(json.dumps(b.input, ensure_ascii=False))
        return total

    # ===== §2+§3 checkpoint 执行 =====

    async def checkpoint(self, *, terminal: bool = False) -> list[Finding]:
        """执行一次 checkpoint: spawn reviewer + 持久化 findings + 入队通知。

        对照原版 checkpoint (0850.js:908) + spawnReviewersAndAwait (2023)。
        返回这次产出的 findings。
        """
        if not self.enabled:
            return []

        v = self.last_checkpoint_idx
        W = len(self.frame.messages)
        if W <= v:
            return []

        # 更新 checkpoint 指针
        self.last_checkpoint_idx = W
        if not terminal:
            self.last_checkpoint_at = time.time() * 1000

        # 构建 reviewer prompt (§8)
        prompt = self._build_review_prompt(v, W, terminal)
        if not prompt.strip():
            return []

        # spawn reviewer (简化: 直接调 LLM, 不 spawn 子 frame)
        self.reviewer_busy = True
        try:
            findings = await self._run_reviewer(prompt)
        finally:
            self.reviewer_busy = False

        # 持久化 findings (§4 → verification_checks)
        self._persist_checks(findings, v, W)

        # 入队 fail/warn 通知 (§5)
        self._queue_notice(findings, [v])

        return findings

    async def _run_reviewer(self, prompt: str) -> list[Finding]:
        """调 reviewer LLM,解析 findings。对照原版 spawnReviewersAndAwait。"""
        reviewer_system = (
            "You are a reviewer auditing an agent's work in a fresh context. Trace each claim against "
            "the transcript and artifacts. Verdict: pass (traced), warn (label mismatch / plan deviation "
            "but valid method), fail (claim didn't happen / substantive contradiction / fabricated citation "
            "/ wrong method). Only report concrete issues; don't flag rounding/paraphrase. "
            "Call submit_output with your findings array. Empty list if all traces cleanly."
        )
        # 加 submit_output 工具
        from operon.llm.messages import ToolDefinition

        submit_tool = ToolDefinition(
            name="submit_output",
            description="Submit your review findings. Call ONCE.",
            parameters=FINDINGS_OUTPUT_SCHEMA,
        )
        try:
            resp = await self.llm.chat(
                [Message(role=Role.USER, content=prompt)],
                system=reviewer_system,
                tools=[submit_tool],
                model=self.reviewer_model,
                max_tokens=4096,
                temperature=0,
            )
        except Exception:
            return []

        # 解析 tool_use 里的 findings
        for b in resp.content:
            if isinstance(b, ToolUseBlock) and b.name == "submit_output":
                return self._parse_findings(b.input)
        # 没调 submit_output → 解析文本
        return []

    def _parse_findings(self, output: dict) -> list[Finding]:
        """解析 submit_output 的 input 成 Finding 列表。"""
        raw = output.get("findings", [])
        findings = []
        for f in raw:
            try:
                v = f.get("verdict", "pass")
                verdict = Verdict(v) if v in ("pass", "warn", "fail") else Verdict.PASS
                findings.append(
                    Finding(
                        msg_idx=f.get("msg_idx", 0),
                        claim=f.get("claim", ""),
                        verdict=verdict,
                        evidence=f.get("evidence", ""),
                        severity=f.get("severity"),
                        artifact_version_id=f.get("artifact_version_id"),
                        reviewer_model=self.reviewer_model,
                    )
                )
            except Exception:
                continue
        return findings

    # ===== §4 持久化 =====

    def _persist_checks(self, findings: list[Finding], start: int, end: int) -> None:
        """写 verification_checks。对照原版 _persistChecks (0850.js:2589)。"""
        from .dispositions import VerificationCheck

        for i, f in enumerate(findings):
            check = VerificationCheck(
                id=str(uuid.uuid4()),
                root_frame_id=self.frame.root_frame_id,
                claim=f.claim,
                verdict=f.verdict.value,
                severity=f.severity,
                evidence=f.evidence,
                reviewer_idx=i,
                reviewer_model=f.reviewer_model,
                reviewer_frame_id=self.frame.id,
                source_ref=f"msg_idx:{f.msg_idx}" if f.msg_idx else "",
            )
            self.checks.append(check)

    # ===== §5 通知回传 =====

    def _queue_notice(self, findings: list[Finding], starts: list[int]) -> None:
        """fail/warn findings 入队。对照原版 queueNoticeFor (0850.js:1960)。"""
        actionable = [f for f in findings if f.is_actionable]
        if not actionable:
            return
        self.pending_notices.append({"starts": starts, "findings": actionable})

    def drain_pending_notices(self) -> dict[str, Any] | None:
        """合并所有 pending notices 成一条通知。对照原版 drainPendingNotices。"""
        if not self.pending_notices:
            return None
        notices = sorted(self.pending_notices, key=lambda n: min(n["starts"]))
        self.pending_notices = []
        starts = sorted({s for n in notices for s in n["starts"]})
        # 按 claim 去重
        seen_claims: set[str] = set()
        all_findings: list[Finding] = []
        for n in notices:
            for f in n["findings"]:
                key = f.claim.strip().lower()
                if key not in seen_claims:
                    seen_claims.add(key)
                    all_findings.append(f)
        text = self._render_notice(all_findings, starts)
        return {"text": text, "findings": all_findings, "starts": starts}

    def _render_notice(self, findings: list[Finding], starts: list[int]) -> str:
        """渲染通知文本。对照原版 kjz (0832.js:127)。"""
        if not findings:
            return ""
        lines = [
            f'[Auditor] <verification_findings starts="{",".join(str(s) for s in starts)}">',
            f"A reviewer traced your work and found {len(findings)} issue(s):",
        ]
        for i, f in enumerate(findings, 1):
            lines.append(f'  {i}. [{f.verdict.value}] "{f.claim}"')
            if f.evidence:
                lines.append(f"     {f.evidence}")
        lines.append("")
        lines.append("Acknowledge in one line and make the fix (or rebut if a finding is wrong).")
        lines.append("</verification_findings>")
        return "\n".join(lines)

    # ===== §8 reviewer prompt =====

    def _build_review_prompt(self, start: int, end: int, terminal: bool) -> str:
        """构建 reviewer prompt。对照原版 buildPayloadParts (0850.js:2416)。"""
        msgs = self.frame.messages[start:end]
        if not msgs:
            return ""
        # 序列化 transcript
        transcript_lines = []
        for i, m in enumerate(msgs):
            role = m.role.value
            if isinstance(m.content, str):
                text = m.content
            else:
                parts = []
                for b in m.content:
                    if isinstance(b, TextBlock):
                        parts.append(b.text)
                    elif isinstance(b, ToolUseBlock):
                        parts.append(f"[tool_use {b.name}] {b.input}")
                    elif isinstance(b, ToolResultBlock):
                        c = b.content
                        parts.append(f"[tool_result] {c if isinstance(c, str) else '(object)'}")
                text = "\n".join(parts)
            transcript_lines.append(f"[{i}] [{role}] {text[:2000]}")
        transcript = "\n\n".join(transcript_lines)[:60000]  # 截断

        return (
            f"You are reviewing work an agent did in frame {self.frame.id}. "
            f"{'It ended its turn.' if terminal else ''}\n"
            f"Window is [{start}..{end}].\n\n"
            f"## Transcript\n{transcript}\n\n"
            f"## Your task\n"
            f"Trace each substantive claim in the transcript against the evidence. "
            f"Report findings via submit_output. Each finding: {{msg_idx, claim, verdict, evidence}}.\n"
            f"Verdict: pass=traced, warn=label/plan mismatch but valid, fail=fabricated/contradictory/wrong.\n"
            f"Empty findings list if everything traces cleanly."
        )

    # ===== §6 terminal barrier =====

    async def terminal_barrier(self) -> dict[str, Any]:
        """自然完成时的最终审查。对照原版 _gateReviewerTerminalBarrier (0871.js:1836)。

        Returns: {veto: bool, notice: dict|None}
        veto=True 表示应阻止完成,继续给 agent 一轮修复。
        """
        if not self.enabled:
            return {"veto": False, "notice": None}
        G = self.last_checkpoint_idx
        if len(self.frame.messages) - G > 0:
            await self.checkpoint(terminal=True)
        notice = self.drain_pending_notices()
        if notice and self.verification_bounces < self.config.max_consecutive_bounces:
            self.verification_bounces += 1
            return {"veto": True, "notice": notice}
        return {"veto": False, "notice": None}
