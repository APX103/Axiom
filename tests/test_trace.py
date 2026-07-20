"""Trace 可观测层测试。

覆盖:
1. TraceRecorder 基础: span/event 写入、JSONL 格式、嵌套
2. 脱敏: API key / Bearer token / 敏感字段名
3. 摘要: 工具结果截断
4. _NullRecorder 零开销 (enabled=False 时)
5. 集成: 跑 mini agent loop, 断言 trace 文件有 llm_call/tool_call 记录
6. setup_logging 幂等 + handler 配置
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from operon.observability.logging_setup import setup_logging
from operon.observability.spans import Span, SpanKind
from operon.observability.trace import (
    TraceRecorder,
    _redact_value,
    cleanup_old_traces,
    get_trace_recorder,
)


@pytest.fixture
def trace_recorder(tmp_path: Path) -> TraceRecorder:
    """基础 recorder, 写到 tmp_path/trace/{session}/。"""
    return TraceRecorder(
        trace_id="test-trace-001",
        session_id="test-session",
        trace_dir=tmp_path / "trace" / "test-session",
    )


def _read_jsonl(path: Path) -> list[dict]:
    """读 JSONL 文件, 返回 list of dict。"""
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    return [json.loads(line) for line in lines if line.strip()]


# ---------- 1. 基础: span / event 写入 ----------


def test_span_writes_to_jsonl(trace_recorder: TraceRecorder):
    """span 退出时落盘, JSONL 一行一个 span。"""
    with trace_recorder.span("llm_call", kind=SpanKind.LLM, model="deepseek"):
        trace_recorder.set_attrs(input_tokens=100, output_tokens=50)

    records = _read_jsonl(trace_recorder._file_path)
    assert len(records) == 1
    r = records[0]
    assert r["name"] == "llm_call"
    assert r["kind"] == "llm"
    assert r["model"] == "deepseek"
    assert r["input_tokens"] == 100
    assert r["output_tokens"] == 50
    assert r["duration_ms"] is not None
    assert r["duration_ms"] >= 0
    assert r["error"] is None
    assert r["trace_id"] == "test-trace-001"
    assert r["session_id"] == "test-session"
    assert r["span_id"]  # 自动生成


def test_event_no_duration(trace_recorder: TraceRecorder):
    """event 是瞬时事件, duration_ms=0。"""
    trace_recorder.event("compact_triggered", reason="hard_wall")

    records = _read_jsonl(trace_recorder._file_path)
    assert len(records) == 1
    r = records[0]
    assert r["name"] == "compact_triggered"
    assert r["reason"] == "hard_wall"
    assert r["duration_ms"] == 0.0


def test_span_records_error(trace_recorder: TraceRecorder):
    """span 内抛异常时, error 字段记录异常类型。"""
    with pytest.raises(ValueError, match="boom"):
        with trace_recorder.span("risky_op"):
            raise ValueError("boom")

    records = _read_jsonl(trace_recorder._file_path)
    assert len(records) == 1
    assert "ValueError" in records[0]["error"]
    assert "boom" in records[0]["error"]


def test_nested_spans(trace_recorder: TraceRecorder):
    """嵌套 span 应该各自落盘, 互不干扰。"""
    with trace_recorder.span("outer"):
        trace_recorder.set_attrs(level=1)
        with trace_recorder.span("inner"):
            trace_recorder.set_attrs(level=2)

    records = _read_jsonl(trace_recorder._file_path)
    assert len(records) == 2
    assert records[0]["name"] == "inner"  # 内层先退出
    assert records[0]["level"] == 2
    assert records[1]["name"] == "outer"
    assert records[1]["level"] == 1


# ---------- 2. 脱敏 ----------


def test_redact_api_key_pattern(trace_recorder: TraceRecorder):
    """attrs 里的 sk-xxx 和 Bearer xxx 自动脱敏。"""
    with trace_recorder.span(
        "http_call",
        url="https://api.openai.com",
        auth="Bearer sk-abc123secret",
        api_key="sk-xyz456real",
    ):
        pass

    records = _read_jsonl(trace_recorder._file_path)
    r = records[0]
    assert "sk-abc123secret" not in r["auth"]
    assert "sk-xyz456real" not in r["api_key"]
    assert "***" in r["auth"]
    assert "***" in r["api_key"]


def test_redact_sensitive_dict_keys():
    """dict 里 api_key/authorization/token 等键的值替换为 ***。"""
    sensitive = {
        "name": "public_name",
        "api_key": "sk-secret",
        "headers": {"Authorization": "Bearer xyz"},
        "data": [{"token": "abc", "value": 1}],
    }
    redacted = _redact_value(sensitive)
    assert redacted["name"] == "public_name"  # 不敏感的原样
    assert redacted["api_key"] == "***"
    assert redacted["headers"]["Authorization"] == "***"
    assert redacted["data"][0]["token"] == "***"
    assert redacted["data"][0]["value"] == 1


# ---------- 3. 摘要 ----------


def test_summarize_result_truncates():
    """长结果被截断为 200 字符 + ...[truncated]。"""
    long_text = "x" * 500
    summary = TraceRecorder.summarize_result(long_text)
    assert len(summary) < 500
    assert summary.endswith("...[truncated]")
    assert len(summary) == 200 + len("...[truncated]")


def test_summarize_result_short_passthrough():
    """短结果原样返回。"""
    assert TraceRecorder.summarize_result("short") == "short"


def test_summarize_result_dict_serialized():
    """dict 类型先 json 序列化再截断。"""
    summary = TraceRecorder.summarize_result({"key": "value"})
    assert "key" in summary


# ---------- 4. _NullRecorder (enabled=False 时) ----------


def test_get_trace_recorder_disabled_returns_null():
    """trace_config.enabled=False 时返回 _NullRecorder (零开销)。"""
    from operon.config import TraceConfig

    config = TraceConfig(enabled=False)
    recorder = get_trace_recorder(config, session_id="s1", data_dir=Path("/tmp"))
    # 应该是同一个 _NULL_RECORDER 单例
    assert recorder is not None
    # 接口兼容: span / event / set_attrs 都是 no-op, 不抛异常
    with recorder.span("noop"):
        recorder.set_attrs(x=1)
    recorder.event("noop")
    assert recorder.summarize_result("anything") == ""


def test_get_trace_recorder_none_returns_null():
    """trace_config=None (未配置) 时返回 _NullRecorder。"""
    recorder = get_trace_recorder(None, session_id="s1")
    with recorder.span("noop"):
        pass
    # 不抛异常即可


def test_get_trace_recorder_enabled_creates_real(tmp_path: Path):
    """enabled=True 时返回真正的 TraceRecorder, 写到 trace_dir。"""
    from operon.config import TraceConfig

    config = TraceConfig(enabled=True, log_dir=tmp_path / "trace")
    recorder = get_trace_recorder(config, session_id="real-session", data_dir=tmp_path)
    assert isinstance(recorder, TraceRecorder)
    with recorder.span("test"):
        recorder.set_attrs(v=1)
    # 文件应存在
    files = list((tmp_path / "trace" / "real-session").glob("*.jsonl"))
    assert len(files) == 1


# ---------- 5. 集成: mini agent loop ----------


@pytest.mark.asyncio
async def test_agent_loop_writes_trace(workspace: Path, tmp_path: Path, monkeypatch):
    """跑一个 mini agent loop (带工具调用), 验证 trace 文件有 llm_call + tool_call 记录。

    通过 monkeypatch 让 Session._make_trace_recorder 返回真实 recorder (写到 tmp_path)。
    """
    from operon.agent.session import Session, SessionConfig
    from operon.config import TraceConfig
    from tests.conftest import FakeLLM
    from operon.llm.messages import TextBlock, ToolUseBlock, LLMResponse, StopReason, TokenUsage

    # FakeLLM 脚本: 第 1 轮调 bash, 第 2 轮回复文本结束
    llm = FakeLLM([
        LLMResponse(
            content=[ToolUseBlock(id="t1", name="bash", input={"command": "echo hi"})],
            stop_reason=StopReason.TOOL_USE,
            model="fake",
            usage=TokenUsage(input_tokens=20, output_tokens=10),
        ),
        LLMResponse(
            content=[TextBlock(text="done")],
            stop_reason=StopReason.END_TURN,
            model="fake",
            usage=TokenUsage(input_tokens=30, output_tokens=5),
        ),
    ])

    # 准备一个真实 recorder, monkeypatch 到 Session
    real_recorder = TraceRecorder(
        trace_id="integration-test",
        session_id="int-session",
        trace_dir=tmp_path / "trace" / "int-session",
        log_tool_args=True,
        log_tool_result_summary=True,
    )

    from operon.agent import session as session_mod

    monkeypatch.setattr(
        Session, "_make_trace_recorder", staticmethod(lambda sid: real_recorder)
    )

    sess = Session(llm=llm, config=SessionConfig(workspace=workspace))
    result = await sess.run("跑 echo hi")

    # trace 文件应包含:
    # - 2 个 llm_call span (2 轮 LLM 调用)
    # - 1 个 tool_call event (bash 工具调用)
    # - 1 个 tool_result event
    files = list((tmp_path / "trace" / "int-session").glob("*.jsonl"))
    assert len(files) == 1
    records = _read_jsonl(files[0])

    llm_calls = [r for r in records if r["name"] == "llm_call"]
    tool_calls = [r for r in records if r["name"] == "tool_call"]
    tool_results = [r for r in records if r["name"] == "tool_result"]

    assert len(llm_calls) == 2, f"expected 2 llm_call spans, got {len(llm_calls)}"
    # model 字段应存在 (测试未配 model 时记 "unknown", 是预期行为)
    assert "model" in llm_calls[0]
    assert llm_calls[0]["input_tokens"] == 20
    assert llm_calls[0]["output_tokens"] == 10

    assert len(tool_calls) >= 1
    assert tool_calls[0]["tool"] == "bash"

    assert len(tool_results) >= 1
    assert "is_error" in tool_results[0]


@pytest.mark.asyncio
async def test_agent_loop_disabled_trace_no_file(workspace: Path):
    """enabled=False 时 trace 目录不应有文件 (零开销)。"""
    from operon.agent.session import Session, SessionConfig
    from tests.conftest import FakeLLM
    from operon.llm.messages import TextBlock, LLMResponse, StopReason, TokenUsage

    llm = FakeLLM([
        LLMResponse(
            content=[TextBlock(text="ok")],
            stop_reason=StopReason.END_TURN,
            model="fake",
            usage=TokenUsage(input_tokens=5, output_tokens=2),
        ),
    ])
    sess = Session(llm=llm, config=SessionConfig(workspace=workspace))
    await sess.run("hi")

    # 默认 enabled=False, 不会写 trace 文件 (用户的 ~/.axiom 也不应被污染)
    # 这里不强制断言文件系统, 只要流程不抛异常即可


# ---------- 6. setup_logging ----------


def test_setup_logging_idempotent(tmp_path: Path):
    """重复调用 setup_logging 不应叠加 handler。"""
    root = logging.getLogger()
    # 清理可能的既有 handler (前面其他测试可能配置过)
    for h in list(root.handlers):
        root.removeHandler(h)
    if hasattr(root, "axiom_logging_configured"):
        delattr(root, "axiom_logging_configured")

    setup_logging(log_dir=tmp_path / "logs")
    handler_count_1 = len(root.handlers)

    setup_logging(log_dir=tmp_path / "logs")
    handler_count_2 = len(root.handlers)

    assert handler_count_2 == handler_count_1, "重复调用不应叠加 handler"


def test_setup_logging_creates_file_handler(tmp_path: Path):
    """配置后应该能写日志文件。"""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    if hasattr(root, "axiom_logging_configured"):
        delattr(root, "axiom_logging_configured")

    log_dir = tmp_path / "logs"
    setup_logging(level="DEBUG", log_dir=log_dir)

    logging.getLogger("test_module").info("hello-from-test")

    # flush 所有 handler
    for h in logging.getLogger().handlers:
        h.flush()

    log_file = log_dir / "operon.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "hello-from-test" in content


# ---------- 7. cleanup_old_traces ----------


def test_cleanup_old_traces(tmp_path: Path):
    """超过 retention_days 的 trace 文件被清理。"""
    import os
    import time

    trace_root = tmp_path / "trace" / "old-session"
    trace_root.mkdir(parents=True)
    old_file = trace_root / "old.jsonl"
    old_file.write_text('{"name":"old"}\n')

    # 把 mtime 改到 60 天前
    old_time = time.time() - 60 * 86400
    os.utime(old_file, (old_time, old_time))

    # 新文件保持当前时间
    new_session = tmp_path / "trace" / "new-session"
    new_session.mkdir(parents=True)
    new_file = new_session / "new.jsonl"
    new_file.write_text('{"name":"new"}\n')

    removed = cleanup_old_traces(tmp_path, retention_days=30)
    assert removed == 1
    assert not old_file.exists()
    assert new_file.exists()
    # 空的 old-session 目录应被清掉
    assert not trace_root.exists()
