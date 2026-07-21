"""Unified internal message model.

设计决策（见 docs/divergences.md §2）:
内部消息模型采用 Anthropic-style 的 content-block 语义,因为 agent 状态机
(
与 OpenAI 兼容 API 通信时,由 message_adapter 做格式转换。


frame_messages 表 (0110.js:134),msg_json 字段即序列化的消息。

Block 类型
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class BlockType(StrEnum):
    """Content block 类型。"""

    TEXT = "text"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    IMAGE = "image"
    # document block (PDF vision path) — 首版后置,见 divergences §4
    DOCUMENT = "document"


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    """模型发起的工具调用。

    id: 工具调用唯一标识 (模型生成,如 "toolu_...")
    name: 工具名
    input: 工具参数 (已解析的 dict)
    """

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ToolResultBlock(BaseModel):
    """工具执行结果,回填给模型。

    tool_use_id: 对应的 ToolUseBlock.id
    content: 结果内容 (文本或嵌套 block)
    is_error: 是否为执行错误
    """

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | list[dict[str, Any]]
    is_error: bool = False


class ThinkingBlock(BaseModel):
    """扩展思考块。国内模型支持参差,首版可选。"""

    type: Literal["thinking"] = "thinking"
    thinking: str


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock | ThinkingBlock


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    """一条对话消息。

    role: system / user / assistant
    content: content blocks 列表,或纯文本字符串 (内部规范化为 blocks)

    RC 扩展字段 (Rolling Compact 用,默认空,不破坏现有用法):
    uuid: 消息唯一标识 (RC 折叠追踪用)
    rolling_summary: 折叠摘要元数据 (仅 summary 消息有)
    server_input_tokens: server-side 真实 input token (assistant 锚点用)
    server_output_tokens: server-side 真实 output token
    compact_boundary: 破坏性压缩边界标记
    task_boundary: 任务边界标记 (chunk 切分硬边界)
    _harness_notice: 内部提示标记 (max_tokens 续传等, 不渲染给用户)
    """

    # 允许任意额外字段 (向前兼容)
    model_config = {"extra": "allow"}

    role: Role
    content: str | list[ContentBlock]

    def normalize(self) -> Message:
        """纯文本 content 规范化为 [TextBlock]。"""
        if isinstance(self.content, str):
            return self.model_copy(update={"content": [TextBlock(text=self.content)]})
        return self


class StopReason(StrEnum):
    """停止原因。

    end_turn: 正常结束 (无更多工具调用)
    tool_use: 模型请求调用工具
    max_tokens: 达到输出上限
    pause_turn: 暂停轮次 (原版 pause_turn 信号)
    refusal: 拒绝
    """

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    PAUSE_TURN = "pause_turn"
    REFUSAL = "refusal"


class ToolDefinition(BaseModel):
    """工具定义。

    name: 工具名
    description: 工具描述 (给模型看)
    parameters: JSON Schema 参数定义
    """

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class LLMResponse(BaseModel):
    """一次 LLM 调用的统一响应。

    content: 模型输出的 content blocks
    stop_reason: 停止原因
    model: 实际使用的模型名
    usage: token 用量
    """

    content: list[ContentBlock]
    stop_reason: StopReason
    model: str
    usage: TokenUsage = Field(default_factory=lambda: TokenUsage())


class TokenUsage(BaseModel):
    """token 用量统计。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
