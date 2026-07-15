"""Frame 内存模型。

对应原版: 0110.js:51-133 的 frames 表 + 0877.js:207 FrameService 的运行时对象。
Frame = 一次 agent 调用的执行单元 (不是单条消息)。一个会话是一棵 frame 树。

本模块是运行时内存态,持久化由 FrameService 负责 (写 DB schema.py::Frame)。
对话消息 (Anthropic-style Message[]) 存在内存,frame_messages 表留待后续接入。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from operon.agent.states import FrameStatus
from operon.llm.messages import Message, TokenUsage


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass
class Frame:
    """运行时 Frame。对应原版 FrameService 持有的对象。

    根 frame: parent_frame_id=None, root_frame_id=self.id
    子 frame: parent_frame_id + root_frame_id 都指向父/根
    REVIEWER/BOOKMARKER: is_hidden=True 的子 frame (阶段 3 用)
    """

    id: str = field(default_factory=_uuid)
    parent_frame_id: str | None = None
    root_frame_id: str = field(default="")
    agent_name: str = "MAIN"
    delegate_name: str | None = None
    status: FrameStatus = FrameStatus.PROCESSING
    model: str | None = None
    project_id: str | None = None
    name: str | None = None
    conversation_type: str = "agent"
    is_hidden: bool = False

    # 对话历史 (内存态,对应原版 frame_messages)
    messages: list[Message] = field(default_factory=list)
    # 运行时上下文: plan_mode/verifier_mode/ultra_mode 等会话级标志
    context: dict[str, Any] = field(default_factory=dict)

    # token 累计
    input_tokens: int = 0
    output_tokens: int = 0

    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    completed_at: datetime | None = None

    task_summary: str | None = None

    def __post_init__(self) -> None:
        # 根 frame 的 root = self
        if not self.root_frame_id:
            self.root_frame_id = self.id

    @property
    def is_root(self) -> bool:
        """是否根 frame。原版: parent_frame_id IS NULL。"""
        return self.parent_frame_id is None

    def add_usage(self, usage: TokenUsage) -> None:
        """累计 token 用量。对应原版 frames 表的 input_tokens/output_tokens 累计。"""
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens

    def to_db_dict(self) -> dict[str, Any]:
        """转 DB 写入字典 (对应 schema.py::Frame)。"""
        return {
            "id": self.id,
            "parent_frame_id": self.parent_frame_id,
            "root_frame_id": self.root_frame_id,
            "agent_name": self.agent_name,
            "delegate_name": self.delegate_name,
            "status": self.status.value,
            "model": self.model,
            "project_id": self.project_id,
            "name": self.name,
            "conversation_type": self.conversation_type,
            "is_hidden": self.is_hidden,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "task_summary": self.task_summary,
        }
