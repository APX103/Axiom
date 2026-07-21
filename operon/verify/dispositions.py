"""验证 findings schema + verdict/disposition。


"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Verdict(StrEnum):
    """裁决。对照原版 verdict enum (0835.js:25)。"""

    PASS = "pass"  # 已追踪到
    WARN = "warn"  # artifact 标签不匹配 / 偏离计划但方法有效
    FAIL = "fail"  # 未发生的声明 / 实质矛盾 / 伪造引用 / 方法错误


class CheckStatus(StrEnum):
    """检查记录状态。对照原版 verification_checks.status enum。"""

    OPEN = "open"
    RESOLVED = "resolved"
    UNADDRESSED = "unaddressed"


class DispositionOutcome(StrEnum):
    """prior findings 的处置结果。对照原版 prior_dispositions.outcome。"""

    RESOLVED = "resolved"
    STILL_OPEN = "still_open"
    WONTFIX = "wontfix"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass
class Finding:
    """单条 reviewer finding。对照原版 x8O.findings 元素。

    对应 verification_checks 表的一行。
    """

    msg_idx: int  # 0-based 消息索引 (窗口内)
    claim: str  # 简短标签
    verdict: Verdict
    evidence: str  # 引用 msg_idx / artifact 的证据
    severity: str | None = None  # low/medium/high
    artifact_version_id: str | None = None
    # 写入 verification_checks 时的额外字段
    reviewer_model: str | None = None
    reviewer_idx: int | None = None
    source_ref: str = ""

    @property
    def is_actionable(self) -> bool:
        """fail/warn 才回传 MAIN (对照 queueNoticeFor 过滤)。"""
        return self.verdict in (Verdict.FAIL, Verdict.WARN)


@dataclass
class VerificationCheck:
    """verification_checks 表记录。对照原版 0110.js:977。

    每条记录是 reviewer 对一个 claim 的一次裁决。
    """

    id: str
    root_frame_id: str
    claim: str
    verdict: str  # pass|warn|fail|inconclusive
    severity: str | None = None
    evidence: str | None = None
    rebuttal: str | None = None
    reviewer_idx: int | None = None
    reviewer_model: str | None = None
    reviewer_frame_id: str | None = None
    reviewer_kind: str | None = None
    source_ref: str = ""
    status: str = "open"  # open|resolved|unaddressed
    artifact_version_id: str | None = None
    reflag_count: int | None = None


# ===== Reviewer 的 output schema (对照 x8O, 0835.js:6-93) =====
FINDINGS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["msg_idx", "claim", "verdict", "evidence"],
                "properties": {
                    "msg_idx": {"type": "integer"},
                    "claim": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["pass", "warn", "fail"]},
                    "evidence": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "artifact_version_id": {"type": "string"},
                },
            },
        },
        "prior_dispositions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "outcome", "note"],
                "properties": {
                    "id": {"type": "string"},
                    "outcome": {
                        "type": "string",
                        "enum": ["resolved", "still_open", "wontfix", "out_of_scope"],
                    },
                    "note": {"type": "string"},
                },
            },
        },
    },
}
