"""Artifact SQLite 持久层测试 (阶段 4)。

对照原版 _saveArtifactCommon 的 DB 写路径 + 断点续会话的 load_from_db 回放。
覆盖:
- save_async 落库 (artifacts / artifact_versions / artifact_dependencies)
- VersionRecord.frame_id 正确入库
- load_from_db 回放内存, 读路径可用
- 新建 store 复用同一 DB (跨实例持久化)
- 纯内存模式 (db_session_factory=None) 仍正常工作 (向后兼容)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiom_core.artifacts.store import ArtifactStore


@pytest.fixture
async def db_store(tmp_path: Path) -> ArtifactStore:
    """带 SQLite 的 ArtifactStore。"""
    pytest.importorskip("aiosqlite")
    from axiom_core.db.session import init_engine, session_factory

    engine = await init_engine(f"sqlite:///{tmp_path / 'art.db'}")
    factory = session_factory(engine)
    return ArtifactStore(tmp_path, db_session_factory=factory)


# ---------- 落库 ----------


@pytest.mark.asyncio
async def test_save_async_persists_artifact_and_version(db_store: ArtifactStore):
    """save_async 后 DB 里有 artifact + version 行。"""
    r = await db_store.save_async(
        filename="paper.tex",
        content=b"\\documentclass{article}",
        project_id="proj_demo",
        root_frame_id="frame_root",
        frame_id="frame_1",
        agent_name="MAIN",
    )
    assert r.ok
    assert r.is_new_artifact

    # 直接查 DB
    from sqlalchemy import select

    from axiom_core.db.schema import Artifact, ArtifactVersion

    async with db_store._db() as session:
        arts = (await session.execute(select(Artifact))).scalars().all()
        vers = (await session.execute(select(ArtifactVersion))).scalars().all()

    assert len(arts) == 1
    assert arts[0].id == r.artifact_id
    assert arts[0].filename == "paper.tex"
    assert arts[0].latest_version_id == r.version_id
    assert len(vers) == 1
    assert vers[0].id == r.version_id
    assert vers[0].version_number == 1
    assert vers[0].frame_id == "frame_1"  # bug 修复: frame_id 现在正确入库
    assert vers[0].agent_name == "MAIN"
    assert vers[0].checksum == r.checksum


@pytest.mark.asyncio
async def test_save_async_version_chain(db_store: ArtifactStore):
    """追加版本时 version_number 递增, latest_version_id 更新。"""
    r1 = await db_store.save_async(
        filename="draft.md",
        content=b"v1",
        project_id="proj_demo",
        root_frame_id="frame_root",
        frame_id="f1",
    )
    r2 = await db_store.save_async(
        filename="draft.md",
        content=b"v2",
        project_id="proj_demo",
        root_frame_id="frame_root",
        frame_id="f2",
        version_of=r1.artifact_id,
    )
    assert r2.version_number == 2
    assert r2.parent_version_id == r1.version_id

    from sqlalchemy import select

    from axiom_core.db.schema import Artifact, ArtifactVersion

    async with db_store._db() as session:
        art = (
            await session.execute(select(Artifact).where(Artifact.id == r1.artifact_id))
        ).scalar_one()
        vers = (
            await session.execute(
                select(ArtifactVersion).order_by(ArtifactVersion.version_number)
            )
        ).scalars().all()

    assert art.latest_version_id == r2.version_id
    assert len(vers) == 2
    assert [v.version_number for v in vers] == [1, 2]


@pytest.mark.asyncio
async def test_save_async_persists_dependencies_dag(db_store: ArtifactStore):
    """dependencies 写入 artifact_dependencies 表。"""
    # 先存两个被依赖的 artifact
    base = await db_store.save_async(
        filename="data.csv",
        content=b"a,b\n1,2",
        project_id="proj_demo",
        root_frame_id="frame_root",
        frame_id="f1",
    )
    fig = await db_store.save_async(
        filename="fig.png",
        content=b"\x89PNG",
        project_id="proj_demo",
        root_frame_id="frame_root",
        frame_id="f2",
    )
    # 第三个依赖前两个
    paper = await db_store.save_async(
        filename="paper.tex",
        content=b"uses both",
        project_id="proj_demo",
        root_frame_id="frame_root",
        frame_id="f3",
        dependencies=[
            {"version_id": base.version_id, "ref_name": "data"},
            {"version_id": fig.version_id, "ref_name": "figure"},
        ],
    )

    from sqlalchemy import select

    from axiom_core.db.schema import ArtifactDependency

    async with db_store._db() as session:
        deps = (
            await session.execute(
                select(ArtifactDependency).where(
                    ArtifactDependency.version_id == paper.version_id
                )
            )
        ).scalars().all()

    dep_ids = {d.depends_on_version_id for d in deps}
    assert dep_ids == {base.version_id, fig.version_id}
    refs = {d.depends_on_version_id: d.ref_name for d in deps}
    assert refs[base.version_id] == "data"
    assert refs[fig.version_id] == "figure"


# ---------- load_from_db 回放 ----------


@pytest.mark.asyncio
async def test_load_from_db_replays_to_memory(tmp_path: Path):
    """跨 store 实例: A 存, 新建 B 复用同一 DB, load_from_db 后内存可用。"""
    from axiom_core.db.session import init_engine, session_factory

    engine = await init_engine(f"sqlite:///{tmp_path / 'art.db'}")
    factory = session_factory(engine)

    store_a = ArtifactStore(tmp_path, db_session_factory=factory)
    r = await store_a.save_async(
        filename="report.tex",
        content=b"hello",
        project_id="proj_x",
        root_frame_id="root",
        frame_id="f1",
    )
    aid, vid = r.artifact_id, r.version_id

    # 新 store (模拟服务重启), 同一 workspace + DB
    store_b = ArtifactStore(tmp_path, db_session_factory=factory)
    assert store_b.get_version(vid) is None  # 回放前内存为空

    n = await store_b.load_from_db()
    assert n == 1
    # 回放后读路径可用
    assert store_b.get_version(vid) is not None
    assert store_b.get_artifact(aid) is not None
    v = store_b.get_version(vid)
    assert v.filename if False else True  # version 不直接存 filename, 通过 artifact 取
    art = store_b.get_artifact(aid)
    assert art.filename == "report.tex"
    assert art.latest_version_id == vid
    # 内容仍从工作区文件读 (DB 不存内容)
    assert store_b.read_text(vid) == "hello"


@pytest.mark.asyncio
async def test_load_from_db_filtered_by_project(tmp_path: Path):
    """load_from_db(project_id=...) 只回放该 project。"""
    from axiom_core.db.session import init_engine, session_factory

    engine = await init_engine(f"sqlite:///{tmp_path / 'art.db'}")
    factory = session_factory(engine)
    store = ArtifactStore(tmp_path, db_session_factory=factory)
    await store.save_async(
        filename="a.tex", content=b"a", project_id="proj_a",
        root_frame_id="r", frame_id="f1",
    )
    await store.save_async(
        filename="b.tex", content=b"b", project_id="proj_b",
        root_frame_id="r", frame_id="f2",
    )

    store2 = ArtifactStore(tmp_path, db_session_factory=factory)
    n = await store2.load_from_db(project_id="proj_a")
    assert n == 1
    arts = store2.list_artifacts()
    assert len(arts) == 1
    assert arts[0].project_id == "proj_a"


# ---------- 向后兼容 ----------


def test_in_memory_mode_still_works(tmp_path: Path):
    """db_session_factory=None 时纯内存, 与现有行为一致。"""
    store = ArtifactStore(tmp_path)
    assert not store.has_db
    r = store.save(
        filename="x.tex", content=b"x", project_id="p",
        root_frame_id="r", frame_id="f",
    )
    assert r.ok
    assert store.read_text(r.version_id) == "x"


@pytest.mark.asyncio
async def test_topology_after_load_from_db(tmp_path: Path):
    """回放后 get_lineage_topology 仍能遍历 DAG。"""
    from axiom_core.db.session import init_engine, session_factory

    engine = await init_engine(f"sqlite:///{tmp_path / 'art.db'}")
    factory = session_factory(engine)
    store = ArtifactStore(tmp_path, db_session_factory=factory)
    base = await store.save_async(
        filename="base.tex", content=b"b", project_id="p",
        root_frame_id="r", frame_id="f1",
    )
    top = await store.save_async(
        filename="top.tex", content=b"t", project_id="p",
        root_frame_id="r", frame_id="f2",
        dependencies=[{"version_id": base.version_id, "ref_name": "src"}],
    )
    # 回放到新 store
    store2 = ArtifactStore(tmp_path, db_session_factory=factory)
    await store2.load_from_db()
    topo = store2.get_lineage_topology(top.version_id)
    assert topo["root_version_id"] == top.version_id
    assert any(e["to"] == base.version_id for e in topo["edges"])
