"""Span 数据模型。

一个 span = 一个有起止时间的工作单元 (LLM 调用 / 工具调用 / 一轮迭代)。
落盘到 JSONL 时, 一个 span 占一行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class SpanKind(StrEnum):
    """Span 类型。用于按 kind 过滤/聚合。"""

    TURN = "turn"          # 一轮 agent 迭代
    LLM = "llm"            # LLM 调用
    TOOL = "tool"          # 工具调用
    INTERNAL = "internal"  # 其它内部操作 (compact / memory 等)


@dataclass
class Span:
    """一个工作单元的记录。

    通过 TraceRecorder.span() 上下文管理器创建, 退出时自动补 end_ts / duration。
    """

    trace_id: str
    name: str
    kind: SpanKind
    span_id: str = field(default_factory=lambda: uuid4().hex)
    start_ts: str = field(default_factory=lambda: datetime.now().isoformat())
    end_ts: str | None = None
    duration_ms: float | None = None
    error: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_jsonl_dict(
        self,
        *,
        session_id: str | None,
        frame_id: str | None,
        turn_id: str | None,
    ) -> dict[str, Any]:
        """转换为 JSONL 行 dict。补充共享标识字段, attrs 展平到顶层。

        展平 attrs 让 JSONL 一行可读 (debug 时直接 grep model=... / tool=...)。
        """
        d = {
            "ts": self.start_ts,
            "trace_id": self.trace_id,
            "session_id": session_id,
            "frame_id": frame_id,
            "turn_id": turn_id,
            "span_id": self.span_id,
            "name": self.name,
            "kind": self.kind.value,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }
        # attrs 展平 (与保留字段重名时, 保留字段优先, attrs 丢弃)
        reserved = set(d.keys())
        for k, v in self.attrs.items():
            if k not in reserved:
                d[k] = v
        return d
