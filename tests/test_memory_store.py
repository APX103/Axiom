"""MemoryStore 单元测试 (Layer A)。

覆盖:
- 基础 CRUD: append / replace / remove / list_by_entity / list_all
- Layer A 新接口: list_by_type / list_by_session
- Layer A 新字段: scope / entity_type / meta / session_id / confidence
- 向后兼容: 老式 append(只传 entity+body) 仍能用
- clear_frame 不误删 profile/project
- meta JSON 序列化往返
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from axiom_core.db.schema import Base
from axiom_core.memory.store import MemoryStore, _meta_from_str, _meta_to_str


@pytest.fixture
async def store(tmp_path: Path) -> MemoryStore:
    """临时 SQLite + MemoryStore。"""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return MemoryStore(db_session_factory=factory)


# ---------- 基础 CRUD ----------


@pytest.mark.asyncio
async def test_append_and_list(store: MemoryStore):
    """append 后能 list 出来。"""
    await store.append("project", "first fact")
    await store.append("project", "second fact")
    entries = await store.list_by_entity("project")
    assert len(entries) == 2
    # list 按 created_at desc, 第二条先
    assert "second" in entries[0]["body"]


@pytest.mark.asyncio
async def test_append_truncates_body(store: MemoryStore):
    """body 超 1000 字符被截断。"""
    long = "x" * 2000
    result = await store.append("project", long)
    assert len(result["body"]) == 1000


@pytest.mark.asyncio
async def test_append_empty_body_returns_none(store: MemoryStore):
    """空 body 返回 None, 不写入。"""
    result = await store.append("project", "   ")
    assert result is None
    entries = await store.list_by_entity("project")
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_replace(store: MemoryStore):
    """replace 更新 body。"""
    mem = await store.append("project", "old")
    ok = await store.replace(mem["id"], "new body")
    assert ok
    entries = await store.list_by_entity("project")
    assert entries[0]["body"] == "new body"


@pytest.mark.asyncio
async def test_replace_nonexistent_returns_false(store: MemoryStore):
    """replace 不存在的 id 返回 False。"""
    ok = await store.replace("mem_nonexistent", "x")
    assert not ok


@pytest.mark.asyncio
async def test_remove(store: MemoryStore):
    """remove 删除记忆。"""
    mem = await store.append("project", "to delete")
    ok = await store.remove(mem["id"])
    assert ok
    entries = await store.list_by_entity("project")
    assert len(entries) == 0


# ---------- Layer A 新字段 ----------


@pytest.mark.asyncio
async def test_append_with_layer_a_fields(store: MemoryStore):
    """append 带全部 Layer A 新字段。"""
    result = await store.append(
        "project",
        "kinase 142 is catalytic residue",
        scope="project",
        entity_type="claim",
        evidence="observed",
        origin="agent_inferred",
        session_id="sess_abc",
        confidence=0.85,
        meta={"subject": "kinase 142", "predicate": "is", "object": "catalytic residue"},
    )
    assert result is not None
    assert result["scope"] == "project"
    assert result["entity_type"] == "claim"
    assert result["session_id"] == "sess_abc"
    assert result["confidence"] == 0.85
    assert result["meta"] == {
        "subject": "kinase 142",
        "predicate": "is",
        "object": "catalytic residue",
    }


@pytest.mark.asyncio
async def test_append_backward_compat_scope_defaults_to_entity(store: MemoryStore):
    """老式调用 (只传 entity+body, 不传 scope) 时, scope 默认等于 entity。"""
    result = await store.append("profile", "user prefers Chinese")
    assert result["scope"] == "profile"
    assert result["entity_type"] == "note"  # 默认
    assert result["confidence"] == 0.5  # 默认
    assert result["meta"] is None


@pytest.mark.asyncio
async def test_list_by_type(store: MemoryStore):
    """list_by_type 按语义类型过滤。"""
    await store.append("project", "claim 1", entity_type="claim")
    await store.append("project", "claim 2", entity_type="claim")
    await store.append("project", "evidence 1", entity_type="evidence")
    await store.append("project", "generic note", entity_type="note")

    claims = await store.list_by_type("claim")
    evidences = await store.list_by_type("evidence")
    notes = await store.list_by_type("note")

    assert len(claims) == 2
    assert len(evidences) == 1
    assert len(notes) == 1


@pytest.mark.asyncio
async def test_list_by_session(store: MemoryStore):
    """list_by_session 按来源 session 过滤。"""
    await store.append("project", "from sess A", session_id="sess_a")
    await store.append("project", "also from A", session_id="sess_a")
    await store.append("project", "from sess B", session_id="sess_b")
    await store.append("project", "no session")

    a_mems = await store.list_by_session("sess_a")
    b_mems = await store.list_by_session("sess_b")

    assert len(a_mems) == 2
    assert len(b_mems) == 1
    for m in a_mems:
        assert m["session_id"] == "sess_a"


@pytest.mark.asyncio
async def test_replace_with_meta(store: MemoryStore):
    """replace 可以更新 meta。"""
    mem = await store.append("project", "original", entity_type="claim")
    await store.replace(
        mem["id"], "updated body",
        meta={"subject": "X", "predicate": "is", "object": "Y"},
    )
    entries = await store.list_by_entity("project")
    assert entries[0]["body"] == "updated body"
    assert entries[0]["meta"] == {"subject": "X", "predicate": "is", "object": "Y"}


@pytest.mark.asyncio
async def test_replace_without_meta_keeps_old(store: MemoryStore):
    """replace 不传 meta 时, 原 meta 保持不变。"""
    mem = await store.append(
        "project", "original", entity_type="claim",
        meta={"subject": "X", "predicate": "is", "object": "Y"},
    )
    await store.replace(mem["id"], "new body")  # 不传 meta
    entries = await store.list_by_entity("project")
    assert entries[0]["meta"] == {"subject": "X", "predicate": "is", "object": "Y"}


# ---------- clear_frame 隔离 ----------


@pytest.mark.asyncio
async def test_clear_frame_only_deletes_frame_layer(store: MemoryStore):
    """clear_frame 只删 frame 层, profile/project 不受影响。"""
    await store.append("profile", "user fact")
    await store.append("project", "project fact")
    await store.append("frame", "frame note 1", frame_id="frame_1")
    await store.append("frame", "frame note 2", frame_id="frame_1")
    await store.append("frame", "other frame", frame_id="frame_2")

    deleted = await store.clear_frame("frame_1")
    assert deleted == 2

    # profile/project 还在
    assert len(await store.list_by_entity("profile")) == 1
    assert len(await store.list_by_entity("project")) == 1
    # frame_1 的没了, frame_2 的还在
    frame_all = await store.list_by_entity("frame")
    assert len(frame_all) == 1
    assert frame_all[0]["frame_id"] == "frame_2"


# ---------- mark_surfaced ----------


@pytest.mark.asyncio
async def test_mark_surfaced(store: MemoryStore):
    """mark_surfaced 更新 last_surfaced_at。"""
    m1 = await store.append("project", "fact 1")
    m2 = await store.append("project", "fact 2")
    await store.mark_surfaced([m1["id"]])

    all_mems = await store.list_all()
    surfaced = {m["id"]: m for m in all_mems}
    assert surfaced[m1["id"]]["last_surfaced_at"] is not None
    assert surfaced[m2["id"]]["last_surfaced_at"] is None


# ---------- meta 序列化 helpers ----------


def test_meta_to_str_roundtrip():
    """meta dict → JSON 字符串 → dict 往返无损。"""
    original = {"subject": "X", "predicate": "is", "object": "Y", "nested": {"a": 1}}
    s = _meta_to_str(original)
    assert isinstance(s, str)
    restored = _meta_from_str(s)
    assert restored == original


def test_meta_to_str_none():
    """None 透传。"""
    assert _meta_to_str(None) is None
    assert _meta_from_str(None) is None
    assert _meta_from_str("") is None


def test_meta_from_str_invalid_json():
    """非法 JSON 返回 None, 不抛异常。"""
    assert _meta_from_str("not json") is None
    assert _meta_from_str("{broken") is None


def test_meta_handles_unicode():
    """中文内容能正确序列化 (ensure_ascii=False)。"""
    original = {"subject": "激酶 142 位点", "predicate": "是", "object": "催化残基"}
    s = _meta_to_str(original)
    assert "激酶" in s  # 不是 \u 转义
    assert _meta_from_str(s) == original


# ---------- 无 DB 时的降级 ----------


@pytest.mark.asyncio
async def test_no_db_factory_returns_empty():
    """无 DB factory 时所有操作降级 (返回空/false), 不抛异常。"""
    s = MemoryStore(db_session_factory=None)
    assert await s.list_all() == []
    assert await s.list_by_entity("profile") == []
    assert await s.append("project", "x") is None
    assert await s.replace("any", "x") is False
    assert await s.remove("any") is False
    assert await s.clear_frame("any") == 0
