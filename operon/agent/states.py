"""Frame 状态枚举。


这些集合照搬原版的分组,UI 和状态机依赖它们判断 frame 是否可继续。
"""

from __future__ import annotations

from enum import Enum


class FrameStatus(str, Enum):
    """Frame 状态。对照原版 0011.js:126-137 Hc_ 枚举,值严格一致。"""

    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SUCCESS = "success"
    REPLACED = "replaced"
    CANCELLED = "cancelled"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    AWAITING_USER_RESPONSE = "awaiting_user_response"


# 终态集合 — 照搬原版 H3 (0011.js:136)。进入终态后 frame 不再变化。
TERMINAL: frozenset[FrameStatus] = frozenset({
    FrameStatus.COMPLETED,
    FrameStatus.FAILED,
    FrameStatus.SUCCESS,
    FrameStatus.REPLACED,
    FrameStatus.CANCELLED,
})

# 成功态 — 照搬原版 Ec_。
SUCCESSFUL: frozenset[FrameStatus] = frozenset({FrameStatus.COMPLETED, FrameStatus.SUCCESS})

# 需要输入态 — 照搬原版 bd_ (0131.js:7)。UI 据此显示 "needsInput"。
NEEDS_INPUT: frozenset[FrameStatus] = frozenset({
    FrameStatus.AWAITING_PLAN_APPROVAL,
    FrameStatus.AWAITING_USER_RESPONSE,
})

# 运行态 — 照搬原版 raw (0815.js:116)。
RUNNING: frozenset[FrameStatus] = frozenset({FrameStatus.PROCESSING, FrameStatus.AWAITING_USER_RESPONSE})


class RunResultKind(str, Enum):
    """agent run 的最终结果类型。

    natural:     正常完成 (无更多工具调用)
    cancelled:   被取消 (abort signal)
    awaiting:    等待用户输入 (ask_user / plan 审批)
    max_iters:   达到最大迭代数
    error:       错误退出
    """

    NATURAL = "natural"
    CANCELLED = "cancelled"
    AWAITING = "awaiting"
    MAX_ITERS = "max_iters"
    ERROR = "error"


class StopReason(int, Enum):
    """停止原因 (本地用,与 LLM StopReason 区分)。

    CONTINUE: 继续 (工具已执行,进入下一轮)
    EXIT:     退出循环
    """

    CONTINUE = 0
    EXIT = 1
