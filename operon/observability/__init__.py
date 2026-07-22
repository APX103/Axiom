"""可观测性模块。

提供两件事:
1. 统一 logging 配置 (整个项目原本只有 getLogger 没有 handler 配置,
   所有 info/debug 默认不输出, 只有 warning/exception 到 stderr)。
2. 结构化 trace 记录器: append-only JSONL, 记录 LLM 调用、工具调用的 span,
   用于调试 agent 行为 (长会话出问题时回溯"为什么卡住"、"为什么这个工具调了 5 次")。

设计立场 (轻量 trace, 不做全量采集):
- 只做后端落盘, 不接前端流 (保守起步, 先服务调试)
- JSONL 而非 SQLite (避免 schema 迁移负担, append-only 天然适合)
- 默认关闭 (enabled=False), 避免性能影响
"""

from operon.observability.logging_setup import setup_logging
from operon.observability.spans import Span, SpanKind
from operon.observability.trace import TraceRecorder, get_trace_recorder

__all__ = [
    "TraceRecorder",
    "get_trace_recorder",
    "setup_logging",
    "Span",
    "SpanKind",
]
