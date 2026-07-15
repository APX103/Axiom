"""消息格式转换器: 内部 Anthropic-style ↔ OpenAI 兼容。


重设计点 (divergences §1, §2): 换 OpenAI 兼容 API 后必须做 tool_use ↔
function_calling 的双向转换。

OpenAI 兼容格式要点:
- assistant 消息的 tool_calls 是顶层字段 [{id, type:"function", function:{name, arguments(JSON string)}}]
- tool 结果是单独的 role="tool" 消息,带 tool_call_id
- arguments 是 JSON 字符串 (不是 dict),需 json.loads/dumps
"""

from __future__ import annotations

import json
from typing import Any

from .messages import (
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


def _block_to_dict(block: ContentBlock) -> dict[str, Any]:
    return block.model_dump()


def tools_to_openai(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """内部 ToolDefinition 列表 → OpenAI tools 字段。

    OpenAI 格式: [{"type":"function","function":{"name","description","parameters"}}]
    """
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


def messages_to_openai(
    messages: list[Message], system: str | None
) -> list[dict[str, Any]]:
    """内部消息列表 → OpenAI messages 字段。

    转换规则:
    1. system → {"role":"system","content":...} 放最前
    2. user 消息: content blocks 拼成文本 (首版图片/document 后置)
    3. assistant 消息:
       - text blocks → content 字段
       - tool_use blocks → tool_calls 顶层字段
    4. tool_result blocks → 拆成独立的 {"role":"tool"} 消息
       (OpenAI 要求 tool result 是单独消息,不能塞进 assistant/user)

    注意: 内部模型里 tool_result 通常作为 user 消息的 content block (Anthropic 风格),
    转换时需提取出来。
    """
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})

    for msg in messages:
        msg = msg.normalize()
        if msg.role == Role.SYSTEM:
            text = _extract_text(msg.content)
            if text:
                out.append({"role": "system", "content": text})
            continue

        if msg.role == Role.USER:
            # user 消息可能含 tool_result block (Anthropic 风格) 或纯 text
            tool_results: list[ToolResultBlock] = []
            text_parts: list[str] = []
            for b in msg.content:
                if isinstance(b, ToolResultBlock):
                    tool_results.append(b)
                elif isinstance(b, TextBlock):
                    text_parts.append(b.text)

            # tool_result → 独立的 role=tool 消息 (必须紧跟在对应 assistant tool_calls 后)
            for tr in tool_results:
                content = tr.content if isinstance(tr.content, str) else json.dumps(tr.content, ensure_ascii=False)
                out.append({
                    "role": "tool",
                    "tool_call_id": tr.tool_use_id,
                    "content": content,
                })

            # 文本部分作为 user 消息
            if text_parts:
                out.append({"role": "user", "content": "\n".join(text_parts)})

        elif msg.role == Role.ASSISTANT:
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for b in msg.content:
                if isinstance(b, TextBlock):
                    text_parts.append(b.text)
                elif isinstance(b, ToolUseBlock):
                    tool_calls.append({
                        "id": b.id,
                        "type": "function",
                        "function": {
                            "name": b.name,
                            "arguments": json.dumps(b.input, ensure_ascii=False),
                        },
                    })
                # thinking block 首版丢弃 (OpenAI 兼容协议无标准 thinking 字段)
            entry: dict[str, Any] = {"role": "assistant"}
            if text_parts:
                entry["content"] = "\n".join(text_parts)
            else:
                entry["content"] = None
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
    return out


def _extract_text(blocks: list[ContentBlock]) -> str:
    return "\n".join(b.text for b in blocks if isinstance(b, (TextBlock, ThinkingBlock)) if isinstance(b, TextBlock))


# OpenAI stop_reason → 内部 StopReason
_OPENAI_STOP_MAP = {
    "stop": StopReason.END_TURN,
    "length": StopReason.MAX_TOKENS,
    "tool_calls": StopReason.TOOL_USE,
    "function_call": StopReason.TOOL_USE,
}


def response_from_openai(resp: dict[str, Any]) -> LLMResponse:
    """OpenAI 兼容响应 → 内部 LLMResponse。

    处理:
    1. choices[0].message.reasoning_content → ThinkingBlock (DeepSeek/StepFun/QwQ 等)
    2. choices[0].message.content → TextBlock
    3. choices[0].message.tool_calls → ToolUseBlock[] (arguments JSON 字符串 → dict)
    4. finish_reason → StopReason
    5. usage → TokenUsage (prompt_tokens/completion_tokens)
    """
    from .messages import ThinkingBlock

    choice = resp["choices"][0]
    message = choice["message"]
    finish = choice.get("finish_reason", "stop")

    blocks: list[ContentBlock] = []

    # reasoning_content (thinking) — 很多国内模型通过这个字段返回思考过程
    if (reasoning := message.get("reasoning_content")) and isinstance(reasoning, str):
        blocks.append(ThinkingBlock(thinking=reasoning))

    if (content := message.get("content")) and isinstance(content, str):
        blocks.append(TextBlock(text=content))

    for tc in message.get("tool_calls") or []:
        fn = tc["function"]
        try:
            args = json.loads(fn["arguments"]) if fn["arguments"] else {}
        except json.JSONDecodeError:
            # 模型偶尔输出非法 JSON arguments,保留原始字符串
            args = {"_raw": fn["arguments"]}
        blocks.append(ToolUseBlock(id=tc["id"], name=fn["name"], input=args))

    stop_reason = _OPENAI_STOP_MAP.get(finish, StopReason.END_TURN)

    usage_raw = resp.get("usage") or {}
    usage = TokenUsage(
        input_tokens=usage_raw.get("prompt_tokens", 0),
        output_tokens=usage_raw.get("completion_tokens", 0),
    )

    return LLMResponse(
        content=blocks,
        stop_reason=stop_reason,
        model=resp.get("model", "unknown"),
        usage=usage,
    )


# ===== 流式聚合 =====


class StreamAggregator:
    """聚合 OpenAI 流式 chunk, 产出 text 增量 + 最终 LLMResponse。

    用法:
        agg = StreamAggregator(model="xxx")
        for chunk in sse_chunks:
            text_delta = agg.feed(chunk)   # 返回本 chunk 的 text 增量 (可能为 "")
            if text_delta: yield text_delta
        resp = agg.finalize()              # 流结束, 返回完整 LLMResponse

    OpenAI 流式 chunk 结构: choices[0].delta 含
      - content: text 增量 (str)
      - tool_calls: [{index, id, function:{name, arguments(JSON 增量 str)}}]
      - role: 首个 chunk 标记 "assistant"
    choices[0].finish_reason 在最后 chunk (stop|tool_calls|length)
    usage 可能在最后 chunk (部分 provider 在 stream_options={"include_usage":true} 时给)
    """

    def __init__(self, model: str = "unknown"):
        self.model = model
        self._text_parts: list[str] = []
        self._reasoning_parts: list[str] = []  # thinking/reasoning_content 增量
        # tool_calls 按 index 聚合: {index: {id, name, arguments_str}}
        self._tool_parts: dict[int, dict[str, str]] = {}
        self._finish_reason: str | None = None
        self._usage: dict[str, int] = {}

    def feed(self, chunk: dict[str, Any]) -> tuple[str, str]:
        """喂一个 SSE chunk, 返回 (text_delta, reasoning_delta)。

        两者都可能为 ""。reasoning_delta 是 thinking/reasoning_content 的增量。
        """
        if "model" in chunk and chunk["model"]:
            self.model = chunk["model"]
        choices = chunk.get("choices") or []
        if not choices:
            if "usage" in chunk and chunk["usage"]:
                self._usage = chunk["usage"]
            return "", ""
        choice = choices[0]
        delta = choice.get("delta") or {}
        finish = choice.get("finish_reason")
        if finish:
            self._finish_reason = finish

        # reasoning_content 增量 (thinking)
        reasoning_delta = ""
        if (rc := delta.get("reasoning_content")) and isinstance(rc, str):
            self._reasoning_parts.append(rc)
            reasoning_delta = rc

        # text 增量
        text_delta = ""
        if (c := delta.get("content")) and isinstance(c, str):
            self._text_parts.append(c)
            text_delta = c

        # tool_call 增量
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            slot = self._tool_parts.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] += fn["name"]
            if fn.get("arguments"):
                slot["arguments"] += fn["arguments"]

        if "usage" in chunk and chunk["usage"]:
            self._usage = chunk["usage"]
        return text_delta, reasoning_delta

    def finalize(self) -> LLMResponse:
        """流结束, 返回聚合后的完整 LLMResponse。"""
        from .messages import TextBlock, ThinkingBlock, ToolUseBlock

        blocks: list[ContentBlock] = []

        # thinking 在 text 之前 (模型的推理过程先于回复)
        full_reasoning = "".join(self._reasoning_parts)
        if full_reasoning:
            blocks.append(ThinkingBlock(thinking=full_reasoning))

        full_text = "".join(self._text_parts)
        if full_text:
            blocks.append(TextBlock(text=full_text))

        for idx in sorted(self._tool_parts.keys()):
            slot = self._tool_parts[idx]
            try:
                args = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": slot["arguments"]}
            if slot["id"] or slot["name"]:
                blocks.append(ToolUseBlock(id=slot["id"], name=slot["name"], input=args))

        stop_reason = _OPENAI_STOP_MAP.get(self._finish_reason or "stop", StopReason.END_TURN)
        usage = TokenUsage(
            input_tokens=self._usage.get("prompt_tokens", 0),
            output_tokens=self._usage.get("completion_tokens", 0),
        )
        return LLMResponse(
            content=blocks,
            stop_reason=stop_reason,
            model=self.model,
            usage=usage,
        )
