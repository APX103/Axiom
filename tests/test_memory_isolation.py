"""记忆按 project_id 隔离测试 (Layer A.5)。

验证核心语义:
- project A 的记忆不会被 project B 的 session 召回
- profile 层记忆跨所有 project 可见 (用户偏好共享)
- project_id=NULL 的 project/frame 层记忆视为全局共享 (老数据兼容)

直接测 recall 函数 + MemoryStore.append 的 project_id 处理。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from operon.db.schema import Base
from operon.memory.recall import build_index, recall
from operon.memory.store import MemoryStore


@pytest.fixture
async def store(tmp_path: Path) -> MemoryStore:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return MemoryStore(db_session_factory=factory)


# ---- store.append 的 project_id 处理 ----


@pytest.mark.asyncio
async def test_append_profile_forces_null_project_id(store: MemoryStore):
    """profile 层记忆 project_id 强制 None (跨 project 共享)。"""
    # 即使传了 project_id, profile 层也应该被设为 None
    result = await store.append(
        "profile", "user prefers Chinese",
        scope="profile",
        project_id="proj_aaa",  # 显式传 project_id
    )
    assert result["scope"] == "profile"
    assert result["project_id"] is None  # 被强制 None


@pytest.mark.asyncio
async def test_append_project_layer_keeps_project_id(store: MemoryStore):
    """project 层记忆保留 project_id (实现隔离)。"""
    result = await store.append(
        "project", "kinase 142 is catalytic",
        scope="project",
        project_id="proj_aaa",
    )
    assert result["project_id"] == "proj_aaa"


@pytest.mark.asyncio
async def test_append_frame_layer_keeps_project_id(store: MemoryStore):
    """frame 层记忆保留 project_id。"""
    result = await store.append(
        "frame", "session note",
        scope="frame",
        frame_id="frame_xxx",
        project_id="proj_aaa",
    )
    assert result["project_id"] == "proj_aaa"


@pytest.mark.asyncio
async def test_list_by_project(store: MemoryStore):
    """list_by_project 只返回该 project 的记忆。"""
    await store.append("project", "A1", project_id="proj_a")
    await store.append("project", "A2", project_id="proj_a")
    await store.append("project", "B1", project_id="proj_b")
    # profile 层 project_id 是 None, 不应被任何 list_by_project 捞到
    await store.append("profile", "user fact", scope="profile", project_id="proj_a")

    a = await store.list_by_project("proj_a")
    b = await store.list_by_project("proj_b")

    assert len(a) == 2
    assert len(b) == 1
    assert all(m["project_id"] == "proj_a" for m in a)


# ---- recall 的 project_id 隔离 ----


def _mem(body, scope="project", project_id=None, mem_id=None):
    """构造测试 memory dict (模拟 _row_to_dict 输出)。"""
    return {
        "id": mem_id or f"mem_{hash(body) & 0xFFFFFF:06x}",
        "entity": scope,
        "scope": scope,
        "body": body,
        "project_id": project_id,
    }


def test_recall_isolates_by_project_id():
    """project A 的记忆不会被 project B 召回 (project/frame 层)。"""
    mems = [
        _mem("kinase fact A", project_id="proj_a"),
        _mem("kinase fact B", project_id="proj_b"),
    ]
    idx = build_index(mems)

    # 从 project_a 视角召回
    results_a = recall("kinase", idx, project_id="proj_a")
    # 从 project_b 视角召回
    results_b = recall("kinase", idx, project_id="proj_b")

    bodies_a = {r["body"] for r in results_a}
    bodies_b = {r["body"] for r in results_b}
    assert "kinase fact A" in bodies_a
    assert "kinase fact B" not in bodies_a
    assert "kinase fact B" in bodies_b
    assert "kinase fact A" not in bodies_b


def test_recall_profile_always_visible_across_projects():
    """profile 层记忆跨所有 project 可见 (用户偏好共享)。"""
    mems = [
        _mem("user prefers Chinese", scope="profile", project_id=None),
        _mem("kinase project A fact", project_id="proj_a"),
    ]
    idx = build_index(mems)

    # 两个 project 都能召回 profile 层的 Chinese 偏好
    results_a = recall("Chinese", idx, project_id="proj_a")
    results_b = recall("Chinese", idx, project_id="proj_b")

    bodies_a = {r["body"] for r in results_a}
    bodies_b = {r["body"] for r in results_b}
    assert "user prefers Chinese" in bodies_a
    assert "user prefers Chinese" in bodies_b


def test_recall_null_project_id_is_global_shared():
    """project_id=NULL 的非 profile 记忆视为全局共享 (老数据兼容)。

    老数据迁移前 scope=project/frame 但 project_id=NULL, 不应被任何
    project 隔离掉 (否则老用户升级后看不到自己的旧记忆)。
    """
    mems = [
        _mem("legacy project fact", project_id=None),  # scope=project, project_id=None
    ]
    idx = build_index(mems)

    # 从任何 project 视角都能召回 (NULL = 全局共享)
    results_a = recall("legacy", idx, project_id="proj_a")
    results_b = recall("legacy", idx, project_id="proj_b")

    assert len(results_a) >= 1
    assert len(results_b) >= 1
    assert results_a[0]["body"] == "legacy project fact"


def test_recall_no_project_id_no_filter():
    """recall 不传 project_id 时不做 project 过滤 (向后兼容)。"""
    mems = [
        _mem("A fact", project_id="proj_a"),
        _mem("B fact", project_id="proj_b"),
    ]
    idx = build_index(mems)

    # 不传 project_id: 全部可见
    results = recall("fact", idx)
    bodies = {r["body"] for r in results}
    assert "A fact" in bodies
    assert "B fact" in bodies


def test_recall_project_isolation_with_profile_mixed():
    """混合场景: project 隔离 + profile 共享同时工作。"""
    mems = [
        # 两个 project 各自的 project 层记忆
        _mem("kinase hypothesis A", project_id="proj_a"),
        _mem("kinase hypothesis B", project_id="proj_b"),
        # profile 层 (用户偏好, 跨 project)
        _mem("user works on kinase", scope="profile", project_id=None),
    ]
    idx = build_index(mems)

    # project_a 召回 'kinase': 应该看到自己的 hypothesis A + profile 用户偏好
    results_a = recall("kinase", idx, project_id="proj_a")
    bodies_a = {r["body"] for r in results_a}
    assert "kinase hypothesis A" in bodies_a
    assert "user works on kinase" in bodies_a  # profile 共享
    assert "kinase hypothesis B" not in bodies_a  # B 被隔离
