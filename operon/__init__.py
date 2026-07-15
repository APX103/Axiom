"""operon-py: Operon 的 Python clean-room 复刻。

公开 API:
    from operon import Settings, OpenAICompatClient
    from operon.llm import Message, TextBlock, ToolUseBlock, ToolDefinition

详见 docs/mapping.md (原版↔新模块对照) 与 docs/divergences.md (设计偏离)。
"""

from __future__ import annotations

__version__ = "0.0.1"

from .config import Settings, load_settings
from .llm.base import LLMClient
from .llm.messages import (
    ContentBlock,
    LLMResponse,
    Message,
    Role,
    StopReason,
    TextBlock,
    ThinkingBlock,
    TokenUsage,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from .llm.openai_compat import OpenAICompatClient
from .llm.token_counter import CHARS_PER_TOKEN, TokenCounter, estimate_tokens

__all__ = [
    "__version__",
    # config
    "Settings",
    "load_settings",
    # llm
    "LLMClient",
    "OpenAICompatClient",
    "Message",
    "Role",
    "ContentBlock",
    "TextBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ThinkingBlock",
    "ToolDefinition",
    "LLMResponse",
    "StopReason",
    "TokenUsage",
    "TokenCounter",
    "CHARS_PER_TOKEN",
    "estimate_tokens",
]
