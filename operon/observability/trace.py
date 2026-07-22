"""Trace 记录器。

设计原则 (轻量 trace, 不做全量采集):
- 轻量: 只落 JSONL, 不入 SQLite (避免 schema 迁移负担)
- 异步安全: 用 threading.Lock 保护文件写入 (agent 在 asyncio loop 里跑, 但
  工具可能起线程池; 不上 asyncio.Lock 是为了不让 IO 阻塞 event loop)
- 默认关闭: TraceConfig.enabled=False, 通过 settings 开启
- 脱敏: 默认不记 LLM payload / 工具结果全文, 只记摘要 + 元信息

落盘路径: {data_dir}/trace/{session_id}/{trace_id}.jsonl
- 一个 session 一个目录 (删除 session 时可整体清理)
- 一个 trace (一次完整 agent run) 一个文件
- append-only, 每行一个 span/event, 行级原子写

用法 (agent runner):
    recorder = get_trace_recorder(config, session_id="abc", frame_id="frame_xyz")
    with recorder.span("llm_call", kind=SpanKind.LLM, model="deepseek-chat"):
        resp = await self._call_llm(...)
        recorder.set_attrs(input_tokens=resp.usage.input_tokens, ...)

    recorder.event("compact_triggered", reason="hard_wall")
"""

from __future__ import annotations

import json
import logging
import re
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from operon.observability.spans import Span, SpanKind

logger = logging.getLogger(__name__)

# 敏感字段名 (落盘时这些字段的值会被替换为 ***)
# 覆盖常见 API key / token / Authorization header
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "token",
    "secret",
    "password",
    "x-api-key",
}

# 敏感字符串模式: sk-xxx / Bearer xxx 等
_SENSITIVE_PATTERNS = [
    re.compile(r"(sk-)\S+", re.IGNORECASE),
    re.compile(r"(Bearer\s)\S+", re.IGNORECASE),
]

# 工具结果摘要的最大字符数 (避免大输出撑爆 trace 文件)
_RESULT_SUMMARY_LIMIT = 200


def _redact_value(value: Any) -> Any:
    """递归脱敏一个值。dict 递归, str 应用模式替换, 其它原样返回。"""
    if isinstance(value, dict):
        return {
            k: ("***" if k.lower() in _SENSITIVE_KEYS else _redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, str):
        redacted = value
        for pattern in _SENSITIVE_PATTERNS:
            redacted = pattern.sub(r"\1***", redacted)
        return redacted
    return value


def _summarize_result(result: Any, limit: int = _RESULT_SUMMARY_LIMIT) -> str:
    """把工具结果/LLM 输出截断为带省略号的摘要。"""
    try:
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(result)
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


class TraceRecorder:
    """结构化 trace 记录器 (per-trace, 即一次 agent run 一个实例)。

    线程安全 (内部用 threading.Lock 保护文件写入)。
    """

    def __init__(
        self,
        trace_id: str,
        session_id: str | None,
        trace_dir: Path,
        *,
        log_llm_payload: bool = False,
        log_tool_args: bool = True,
        log_tool_result_summary: bool = True,
    ) -> None:
        self.trace_id = trace_id
        self.session_id = session_id
        self._trace_dir = Path(trace_dir)
        self._log_llm_payload = log_llm_payload
        self._log_tool_args = log_tool_args
        self._log_tool_result_summary = log_tool_result_summary

        self._lock = threading.Lock()
        self._closed = False
        self._frame_id: str | None = None
        self._turn_id: str | None = None

        # 当前 span 栈 (支持嵌套; 栈顶是当前活跃 span)
        # 注意: 这里假设单线程使用 (一个 agent run 在一个 task 里),
        # 跨 task 共享 recorder 时由调用方负责不要并发进 span。
        self._span_stack: list[Span] = []

        self._trace_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._trace_dir / f"{trace_id}.jsonl"

    # ---- 上下文标识 (从 runner 注入) ----

    def set_context(self, *, frame_id: str | None = None, turn_id: str | None = None) -> None:
        """设置当前 span 共享的 frame/turn 标识 (每轮 turn 开始时调一次)。"""
        if frame_id is not None:
            self._frame_id = frame_id
        if turn_id is not None:
            self._turn_id = turn_id

    # ---- Span API ----

    @contextmanager
    def span(self, name: str, *, kind: SpanKind | str = SpanKind.INTERNAL, **attrs):
        """记录一个有起止时间的 span。

        with recorder.span("llm_call", kind=SpanKind.LLM, model="deepseek"):
            ...
            recorder.set_attrs(input_tokens=1234)
        """
        kind_enum = SpanKind(kind) if isinstance(kind, str) else kind
        sp = Span(
            trace_id=self.trace_id,
            name=name,
            kind=kind_enum,
            attrs=_redact_value(dict(attrs)) if attrs else {},
        )
        self._span_stack.append(sp)
        start = datetime.now()
        try:
            yield sp
        except Exception as e:
            sp.error = f"{type(e).__name__}: {e}"
            raise
        finally:
            sp.end_ts = datetime.now().isoformat()
            sp.duration_ms = (datetime.now() - start).total_seconds() * 1000
            self._span_stack.pop()
            self._write(sp.to_jsonl_dict(
                session_id=self.session_id,
                frame_id=self._frame_id,
                turn_id=self._turn_id,
            ))

    def event(self, name: str, **attrs) -> None:
        """记录一个瞬时事件 (无 duration)。"""
        sp = Span(
            trace_id=self.trace_id,
            name=name,
            kind=SpanKind.INTERNAL,
            end_ts=datetime.now().isoformat(),
            duration_ms=0.0,
            attrs=_redact_value(dict(attrs)) if attrs else {},
        )
        self._write(sp.to_jsonl_dict(
            session_id=self.session_id,
            frame_id=self._frame_id,
            turn_id=self._turn_id,
        ))

    # ---- 在 span 内补充 attrs ----

    def set_attrs(self, **attrs) -> None:
        """给当前 span 补 attrs (在 with 块内调用)。

        自动脱敏。如果不在 span 内则忽略 (避免丢数据但不抛)。
        """
        if not self._span_stack:
            return
        current = self._span_stack[-1]
        current.attrs.update(_redact_value(dict(attrs)))

    # ---- 配置开关 (runner 用来决定记多少) ----

    @property
    def log_llm_payload(self) -> bool:
        return self._log_llm_payload

    @property
    def log_tool_args(self) -> bool:
        return self._log_tool_args

    @property
    def log_tool_result_summary(self) -> bool:
        return self._log_tool_result_summary

    @staticmethod
    def summarize_result(result: Any, limit: int = _RESULT_SUMMARY_LIMIT) -> str:
        """工具结果摘要 (供 runner 调用, 同款逻辑)。"""
        return _summarize_result(result, limit)

    # ---- 内部 ----

    def _write(self, record: dict[str, Any]) -> None:
        if self._closed:
            return
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            try:
                with open(self._file_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as e:
                # trace 写失败不能影响主流程, 只 log 一次
                logger.warning("trace write failed: %s", e)

    def close(self) -> None:
        """标记 recorder 关闭, 后续写入被忽略。"""
        self._closed = True


# ---- 工厂 ----

_NOOP_RECORDER: TraceRecorder | None = None


class _NullRecorder:
    """enabled=False 时返回的 no-op recorder (零开销)。

    所有方法都是 no-op, span() 是空上下文管理器。
    用 __getattr__ 兜底, 避免每个方法都写一遍。
    """

    trace_id = ""
    session_id = None

    def __init__(self) -> None:
        self.log_llm_payload = False
        self.log_tool_args = False
        self.log_tool_result_summary = False

    def set_context(self, **kwargs) -> None:  # noqa: ARG002
        pass

    @contextmanager
    def span(self, name: str, *, kind=SpanKind.INTERNAL, **attrs):  # noqa: ARG002
        yield None

    def event(self, name: str, **attrs) -> None:  # noqa: ARG002
        pass

    def set_attrs(self, **attrs) -> None:  # noqa: ARG002
        pass

    @staticmethod
    def summarize_result(result: Any, limit: int = 200) -> str:  # noqa: ARG002
        return ""

    def close(self) -> None:
        pass


_NULL_RECORDER = _NullRecorder()


def get_trace_recorder(
    trace_config: Any | None,
    *,
    session_id: str | None,
    frame_id: str | None = None,
    data_dir: Path | None = None,
) -> TraceRecorder | Any:
    """根据 TraceConfig 创建 recorder。

    enabled=False (默认) 时返回一个 _NullRecorder (零开销, 接口相同)。
    data_dir 为 None 时默认 ~/.axiom/。
    """
    # 默认关闭
    if trace_config is None or not getattr(trace_config, "enabled", False):
        return _NULL_RECORDER

    # 解析 trace 目录
    log_dir = getattr(trace_config, "log_dir", None)
    if log_dir is not None:
        trace_dir = Path(log_dir).expanduser() / (session_id or "_no_session")
    else:
        base = Path(data_dir) if data_dir is not None else Path.home() / ".axiom"
        trace_dir = base / "trace" / (session_id or "_no_session")

    return TraceRecorder(
        trace_id=uuid4().hex,
        session_id=session_id,
        trace_dir=trace_dir,
        log_llm_payload=getattr(trace_config, "log_llm_payload", False),
        log_tool_args=getattr(trace_config, "log_tool_args", True),
        log_tool_result_summary=getattr(trace_config, "log_tool_result_summary", True),
    )


def cleanup_old_traces(data_dir: Path, retention_days: int) -> int:
    """清理超过保留期的 trace 文件。返回删除的文件数。

    在 setup_logging 之后调用一次即可。
    """
    if retention_days <= 0:
        return 0
    trace_root = data_dir / "trace"
    if not trace_root.exists():
        return 0

    cutoff = datetime.now().timestamp() - retention_days * 86400
    removed = 0
    for session_dir in trace_root.iterdir():
        if not session_dir.is_dir():
            continue
        for f in session_dir.glob("*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        # 空目录顺手清掉
        try:
            if session_dir.is_dir() and not any(session_dir.iterdir()):
                session_dir.rmdir()
        except OSError:
            pass
    return removed
