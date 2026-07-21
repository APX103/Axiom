"""operon-py: Operon 的 Python clean-room 复刻。

公开 API:
    from operon import Settings, OpenAICompatClient
    from operon.llm import Message, TextBlock, ToolUseBlock, ToolDefinition

详见 docs/mapping.md (原版↔新模块对照) 与 docs/divergences.md (设计偏离)。
"""

from __future__ import annotations


def _read_version() -> str:
    """从 pyproject.toml 读取版本号, 避免硬编码。"""
    try:
        import tomllib
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


__version__ = _read_version()

# __version__ 有意置于子模块导入之前 (单一版本来源), 故下行导入触发 E402 属预期。
from .config import Settings, load_settings  # noqa: E402
from .llm.base import LLMClient  # noqa: E402
from .llm.messages import (  # noqa: E402
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
from .llm.openai_compat import OpenAICompatClient  # noqa: E402
from .llm.token_counter import CHARS_PER_TOKEN, TokenCounter, estimate_tokens  # noqa: E402

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
