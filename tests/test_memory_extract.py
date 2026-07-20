"""memory extract 单元测试 (Layer A)。

覆盖:
- FakeLLM 返回新 schema JSON, 验证 apply_extraction 正确写入新字段
- _validate_append 枚举校验 (非法 scope/entity_type/origin/evidence 被规范化)
- confidence 限制 [0, 1]
- 解析容错 (markdown fence, JSON 错误返回空 ops)
- meta 透传到 store
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from operon.db.schema import Base
from operon.llm.base import LLMClient
from operon.llm.messages import LLMResponse, Message, StopReason, TextBlock, TokenUsage
from operon.memory.extract import (
    _clamp_confidence,
    _validate_append,
    apply_extraction,
    extract_memories,
)
from operon.memory.store import MemoryStore


class FakeLLM(LLMClient):
    """返回固定 JSON 的假 LLM。"""

    def __init__(self, json_response: str):
        self.json_response = json_response
        self.calls = 0

    async def chat(self, messages, *, system=None, tools=None, model=None,
                   max_tokens=8192, temperature=None, **kw):
        self.calls += 1
        return LLMResponse(
            content=[TextBlock(text=self.json_response)],
            stop_reason=StopReason.END_TURN,
            model="fake",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )

    def count_tokens(self, text):
        return len(text) // 4

    async def close(self):
        pass


@pytest.fixture
async def store(tmp_path: Path) -> MemoryStore:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return MemoryStore(db_session_factory=factory)


# ---------- _validate_append 枚举校验 ----------


def test_validate_append_happy_path():
    """完整合法的 append item 通过校验。"""
    item = {
        "scope": "project",
        "entity_type": "claim",
        "body": "X is Y",
        "evidence": "observed",
        "origin": "agent_inferred",
        "confidence": 0.8,
        "meta": {"subject": "X", "predicate": "is", "object": "Y"},
    }
    result = _validate_append(item)
    assert result["scope"] == "project"
    assert result["entity_type"] == "claim"
    assert result["confidence"] == 0.8
    assert result["meta"] == {"subject": "X", "predicate": "is", "object": "Y"}


def test_validate_append_invalid_scope_falls_back():
    """非法 scope 回退到 project。"""
    result = _validate_append({"scope": "bogus", "body": "x"})
    assert result["scope"] == "project"


def test_validate_append_invalid_entity_type_falls_back():
    """非法 entity_type 回退到 note。"""
    result = _validate_append({"entity_type": "unknown_type", "body": "x"})
    assert result["entity_type"] == "note"


def test_validate_append_invalid_origin_falls_back():
    """非法 origin 回退到 extractor。"""
    result = _validate_append({"origin": "hacker", "body": "x"})
    assert result["origin"] == "extractor"


def test_validate_append_legacy_entity_field():
    """LLM 用老 entity 字段 (没有 scope) 时, scope 从 entity 拷贝。"""
    result = _validate_append({"entity": "profile", "body": "x"})
    assert result["scope"] == "profile"


def test_validate_append_missing_meta_defaults_empty_dict():
    """没传 meta 时默认空 dict (不是 None)。"""
    result = _validate_append({"body": "x"})
    assert result["meta"] == {}


def test_validate_append_meta_not_dict_defaults_empty():
    """meta 不是 dict 时回退空 dict。"""
    result = _validate_append({"body": "x", "meta": "not a dict"})
    assert result["meta"] == {}


# ---------- confidence 限制 ----------


def test_clamp_confidence_normal():
    assert _clamp_confidence(0.5) == 0.5
    assert _clamp_confidence(0.0) == 0.0
    assert _clamp_confidence(1.0) == 1.0


def test_clamp_confidence_out_of_range():
    """超 range 被 clamp 到 [0, 1]。"""
    assert _clamp_confidence(1.5) == 1.0
    assert _clamp_confidence(-0.3) == 0.0


def test_clamp_confidence_invalid_type():
    """非法类型回退到 0.5。"""
    assert _clamp_confidence("not a number") == 0.5
    assert _clamp_confidence(None) == 0.5


# ---------- extract_memories 集成 ----------


@pytest.mark.asyncio
async def test_extract_memories_parses_layer_a_json():
    """LLM 返回 Layer A schema JSON, extract 正确解析。"""
    response_json = json.dumps({
        "append": [
            {
                "scope": "project",
                "entity_type": "claim",
                "body": "kinase 142 is catalytic",
                "evidence": "observed",
                "origin": "agent_inferred",
                "confidence": 0.85,
                "meta": {"subject": "kinase 142", "predicate": "is", "object": "catalytic"},
            },
            {
                "scope": "project",
                "entity_type": "citation",
                "body": "Smith 2023 on kinase",
                "evidence": "observed",
                "origin": "tool_observed",
                "confidence": 0.9,
                "meta": {"doi": "10.xxx", "title": "Kinase", "authors": ["Smith"], "year": 2023},
            },
        ],
        "replace": [],
        "remove": [],
    })
    llm = FakeLLM(response_json)

    from operon.llm.messages import Message, Role
    ops = await extract_memories(
        [Message(role=Role.USER, content="we found kinase 142 is catalytic")],
        existing=[],
        llm=llm,
    )

    assert len(ops["append"]) == 2
    claim = ops["append"][0]
    assert claim["entity_type"] == "claim"
    assert claim["scope"] == "project"
    assert claim["confidence"] == 0.85
    assert claim["meta"]["subject"] == "kinase 142"

    citation = ops["append"][1]
    assert citation["entity_type"] == "citation"
    assert citation["meta"]["doi"] == "10.xxx"


@pytest.mark.asyncio
async def test_extract_memories_handles_markdown_fence():
    """LLM 把 JSON 包在 ```json ... ``` 里也能解析。"""
    fenced = '```json\n{"append": [{"scope": "project", "body": "x"}], "replace": [], "remove": []}\n```'
    llm = FakeLLM(fenced)

    from operon.llm.messages import Message, Role
    ops = await extract_memories(
        [Message(role=Role.USER, content="conversation")],
        existing=[],
        llm=llm,
    )
    assert len(ops["append"]) == 1
    assert ops["append"][0]["body"] == "x"


@pytest.mark.asyncio
async def test_extract_memories_invalid_json_returns_empty():
    """LLM 返回非 JSON 时返回空 ops, 不抛异常。"""
    llm = FakeLLM("this is not json at all")
    from operon.llm.messages import Message, Role

    ops = await extract_memories(
        [Message(role=Role.USER, content="x")],
        existing=[],
        llm=llm,
    )
    assert ops == {"append": [], "replace": [], "remove": []}


@pytest.mark.asyncio
async def test_extract_memories_empty_transcript():
    """空 transcript (无有效消息) 直接返回空 ops, 不调 LLM。"""
    llm = FakeLLM('{"append": [], "replace": [], "remove": []}')
    ops = await extract_memories([], existing=[], llm=llm)
    assert ops == {"append": [], "replace": [], "remove": []}
    assert llm.calls == 0  # 没调 LLM


@pytest.mark.asyncio
async def test_extract_memories_filters_empty_body():
    """body 为空的 append item 被过滤。"""
    response_json = json.dumps({
        "append": [
            {"scope": "project", "body": "valid", "entity_type": "note"},
            {"scope": "project", "body": "   ", "entity_type": "note"},  # 空
            {"scope": "project", "entity_type": "note"},  # 缺 body
        ],
        "replace": [],
        "remove": [],
    })
    llm = FakeLLM(response_json)
    from operon.llm.messages import Message, Role

    ops = await extract_memories(
        [Message(role=Role.USER, content="x")], existing=[], llm=llm,
    )
    assert len(ops["append"]) == 1
    assert ops["append"][0]["body"] == "valid"


# ---------- apply_integration 集成 ----------


@pytest.mark.asyncio
async def test_apply_extraction_writes_layer_a_fields(store: MemoryStore):
    """apply_extraction 把 Layer A 字段正确写入 store。"""
    ops = {
        "append": [
            {
                "scope": "project",
                "entity_type": "claim",
                "body": "kinase 142 is catalytic",
                "evidence": "observed",
                "origin": "agent_inferred",
                "confidence": 0.9,
                "meta": {"subject": "kinase 142", "predicate": "is", "object": "catalytic"},
            },
        ],
        "replace": [],
        "remove": [],
    }

    count = await apply_extraction(store, ops, frame_id="frame_1", session_id="sess_abc")
    assert count == 1

    entries = await store.list_by_entity("project")
    assert len(entries) == 1
    e = entries[0]
    assert e["entity_type"] == "claim"
    assert e["scope"] == "project"
    assert e["session_id"] == "sess_abc"
    assert e["confidence"] == 0.9
    assert e["meta"] == {"subject": "kinase 142", "predicate": "is", "object": "catalytic"}


@pytest.mark.asyncio
async def test_apply_extraction_replace_with_meta(store: MemoryStore):
    """apply_extraction 的 replace 支持 meta 更新。"""
    mem = await store.append(
        "project", "original", entity_type="claim",
        meta={"subject": "X", "predicate": "is", "object": "Y"},
    )
    ops = {
        "append": [],
        "replace": [{
            "id": mem["id"],
            "body": "corrected",
            "meta": {"subject": "X", "predicate": "is", "object": "Z"},
        }],
        "remove": [],
    }
    await apply_extraction(store, ops)

    entries = await store.list_by_entity("project")
    assert entries[0]["body"] == "corrected"
    assert entries[0]["meta"]["object"] == "Z"


@pytest.mark.asyncio
async def test_apply_extraction_remove(store: MemoryStore):
    """apply_extraction 的 remove 正确删除。"""
    mem = await store.append("project", "to remove")
    ops = {"append": [], "replace": [], "remove": [mem["id"]]}
    count = await apply_extraction(store, ops)
    assert count == 1
    assert len(await store.list_by_entity("project")) == 0
