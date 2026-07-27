"""Rolling Compact 状态 + 摘要元数据。


"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RollingSummaryMeta:
    """折叠摘要元数据。

    存在 Message 的 rolling_summary 字段上。标识这条消息是一个"折叠摘要",
    并记录它覆盖哪些原始消息、净释放多少 token。
    """

    id: str  # 短 id (QHz: hex→base36 取前10)
    text: str  # 完整摘要正文
    from_uuid: str  # chunk 起始消息 uuid (L1)
    to_uuid: str  # chunk 结束消息 uuid (L1)
    tokens_freed: int  # 净释放 token = chunk_tokens - final_tokens
    level: int  # 1 (L1) | 2 (L2)
    folds_uuids: list[str] = field(default_factory=list)  # 仅 L2: 被它折叠的 summary uuid 列表
    compression_ratio: float = 1.0  # chunk_tokens / max(1, final_tokens)
    attempt: int = 1  # 重试次数


@dataclass
class RollingCompactState:
    """Rolling Compact 控制状态。

    这是"控制状态"。真正的会话级摘要数据存在 ConversationState 上:
    - messages[] (含 rolling_summary 的折叠消息)
    - applied_summary_uuids[] (已生效的 summary uuid)
    - rc_l1_fold_count (L1 折叠计数)
    """

    pending: Any | None = None  # 当前 in-flight 的 fork 对象
    known_overflow: int | None = None  # API "prompt too long" 时的 token 量
    wall_pressure: bool = False  # hard-wall 触发, 强制 block-await
    ptl_attempts: int = 0  # PTL(prompt too long) 连续计数
    consecutive_fork_failures: int = 0  # applySettledFork 失败计数
    last_turn_est: int | None = None  # 上一轮 projectedTokenEstimate
    # 失败计数表: key=f"{frame_id}:{level}", value={chunk_key, fail_count}
    #
    fail_table: dict[str, dict[str, Any]] = field(default_factory=dict)


def new_rolling_compact_state() -> RollingCompactState:
    """工厂。"""
    return RollingCompactState()


def make_summary_id(uuid_str: str) -> str:
    """短 id。

    给折叠摘要一个人类可读的短 id,模型用 summary_query 时引用。
    """
    try:
        n = int(uuid_str.replace("-", "")[:16], 16)
        # 转 base36
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
        if n == 0:
            return "0"
        out = ""
        while n > 0:
            n, r = divmod(n, 36)
            out = alphabet[r] + out
        return out[:10]
    except (ValueError, IndexError):
        return uuid_str[:10]
