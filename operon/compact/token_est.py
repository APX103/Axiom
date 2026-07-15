"""Rolling Compact token 估算。

对应原版: 0848.js:339-388 (UZ estimateMessageTokens) + 892-930 (xHz projectedTokenEstimate)。

token 估算策略:
1. assistant 且有 server_output_tokens → 信任 server 数 + tool_result 内容字符估算
2. 有 rolling_summary → 用摘要文本长度
3. 通用: 累加各 content block 字符 / CHARS_PER_TOKEN
   - image/document: 默认 8000 token (vision hint)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from operon.llm.messages import Message, TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock

from .constants import CHARS_PER_TOKEN

if TYPE_CHECKING:
    pass

# 视觉块默认 token (对应原版: _vision_token_hint or 8000, 乘 d8 后净 8000)
VISION_DEFAULT_TOKENS = 8000


def estimate_message_tokens(msg: Message) -> int:
    """单条消息 token 估算。对应原版 UZ (0848.js:339-388)。

    优先级:
    1. assistant + server_output_tokens > 0 → output + tool_result 内容字符
    2. rolling_summary → 摘要文本长度
    3. 通用 content 字符累加
    """
    # server 锚定的 assistant (优先级 1)
    server_out = getattr(msg, "server_output_tokens", None)
    if msg.role.value == "assistant" and server_out and server_out > 0:
        # tool_result 内容额外估 (原版: 非工具调用块长度)
        extra = 0
        if isinstance(msg.content, list):
            for b in msg.content:
                if isinstance(b, ToolResultBlock):
                    c = b.content
                    extra += len(c) if isinstance(c, str) else len(json.dumps(c, ensure_ascii=False))
        return server_out + extra // CHARS_PER_TOKEN

    # 折叠摘要 (优先级 2)
    rs = getattr(msg, "rolling_summary", None)
    if rs is not None and hasattr(rs, "text"):
        return len(rs.text) // CHARS_PER_TOKEN

    # 通用 (优先级 3)
    content = msg.content
    if isinstance(content, str):
        return len(content) // CHARS_PER_TOKEN

    chars = 0
    for b in content:
        if isinstance(b, TextBlock):
            chars += len(b.text)
        elif isinstance(b, ThinkingBlock):
            chars += len(b.thinking)
        elif isinstance(b, ToolUseBlock):
            chars += len(json.dumps(b.input, ensure_ascii=False))
        elif isinstance(b, ToolResultBlock):
            c = b.content
            if isinstance(c, str):
                chars += len(c)
            else:
                chars += len(json.dumps(c, ensure_ascii=False))
        else:
            # 未知 block (image/document) — 视觉块按 VISION_DEFAULT
            bdict = b if isinstance(b, dict) else json.loads(b.model_dump_json())
            if isinstance(bdict, dict) and bdict.get("type") in ("image", "document"):
                chars += (bdict.get("_vision_token_hint") or VISION_DEFAULT_TOKENS) * CHARS_PER_TOKEN
            else:
                chars += len(json.dumps(bdict, ensure_ascii=False, default=str))
    return chars // CHARS_PER_TOKEN


def estimate_messages_total(messages: list[Message], *, system_tokens: int = 0) -> int:
    """所有消息 + system 的总 token 估算。

    对应原版 projectedTokenEstimate (xHz) 的简化版。
    原版有 server-anchor 优化 (以最后一次 server input 为基准),这里简化为全量累加。
    """
    total = system_tokens
    for m in messages:
        total += estimate_message_tokens(m)
    return total


def find_last_server_anchored_assistant(messages: list[Message]) -> int | None:
    """找最后一条有 server_input_tokens 的 assistant。对应原版 Jx_。

    返回 index,无则 None。用于投影估算的锚点。
    """
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.role.value == "assistant" and getattr(m, "server_input_tokens", None):
            return i
    return None
