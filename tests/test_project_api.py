"""Project API + session-project 关联测试 (Layer A.5)。

用 FastAPI TestClient + 临时 SQLite 做 in-process 端到端测试。
不依赖真实 LLM provider, 只测 project CRUD + session.project_id 关联。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest.fixture
def app_and_client(tmp_path: Path):
    """构造 app + TestClient, 用临时 DB。"""
    from operon.api.app import create_app
    from operon.db.session import init_engine, session_factory

    db_path = tmp_path / "test.db"
    engine = asyncio.run(init_engine(f"sqlite+aiosqlite:///{db_path}"))
    app = create_app()
    app.state.db_factory = session_factory(engine)
    client = TestClient(app)
    yield app, client, engine
    asyncio.run(engine.dispose())


# ---- Project CRUD ----


def test_list_projects_default_exists(app_and_client):
    """新 DB 启动后, 默认 project 自动创建。"""
    _, client, _ = app_and_client
    r = client.get("/api/projects")
    assert r.status_code == 200
    projects = r.json()
    assert len(projects) >= 1
    assert any(p["id"] == "proj_default" for p in projects)
    default = next(p for p in projects if p["id"] == "proj_default")
    assert default["is_default"] is True
    assert default["name"] == "默认项目"


def test_create_project(app_and_client):
    _, client, _ = app_and_client
    r = client.post("/api/projects", json={"name": "kinase 研究", "description": "kinase 142"})
    assert r.status_code == 200
    p = r.json()
    assert p["name"] == "kinase 研究"
    assert p["description"] == "kinase 142"
    assert p["is_default"] is False
    assert p["id"].startswith("proj_")


def test_update_project_rename(app_and_client):
    _, client, _ = app_and_client
    r = client.post("/api/projects", json={"name": "原名"})
    pid = r.json()["id"]
    r = client.patch(f"/api/projects/{pid}", json={"name": "新名"})
    assert r.status_code == 200
    assert r.json()["name"] == "新名"


def test_update_project_description(app_and_client):
    _, client, _ = app_and_client
    r = client.post("/api/projects", json={"name": "test"})
    pid = r.json()["id"]
    r = client.patch(f"/api/projects/{pid}", json={"description": "新描述"})
    assert r.json()["description"] == "新描述"


def test_update_project_last_session(app_and_client):
    _, client, _ = app_and_client
    r = client.post("/api/projects", json={"name": "test"})
    pid = r.json()["id"]
    r = client.patch(f"/api/projects/{pid}", json={"last_session_id": "sess_abc"})
    assert r.json()["last_session_id"] == "sess_abc"


def test_update_nonexistent_project_404(app_and_client):
    _, client, _ = app_and_client
    r = client.patch("/api/projects/proj_bogus", json={"name": "x"})
    assert r.status_code == 404


def test_delete_default_project_rejected(app_and_client):
    """默认 project 不允许删除。"""
    _, client, _ = app_and_client
    r = client.delete("/api/projects/proj_default")
    assert r.status_code == 400


def test_delete_nonexistent_project_404(app_and_client):
    _, client, _ = app_and_client
    r = client.delete("/api/projects/proj_bogus")
    assert r.status_code == 404


def test_delete_empty_project(app_and_client):
    """空 project 直接删。"""
    _, client, _ = app_and_client
    r = client.post("/api/projects", json={"name": "to delete"})
    pid = r.json()["id"]
    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    # 确认从列表消失
    r = client.get("/api/projects")
    assert not any(p["id"] == pid for p in r.json())


def test_delete_nonempty_project_without_force_409(app_and_client):
    """非空 project 不带 force 拒绝删除。"""
    _, client, _ = app_and_client
    # 直接往 DB 插一条 SessionRecord 关联到新 project
    from operon.db.schema import Project, SessionRecord

    app, _, engine = app_and_client
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def insert():
        async with factory() as db:
            proj = Project(id="proj_test_nonempty", name="test")
            db.add(proj)
            await db.commit()
            # SessionRecord 需要满足 notnull 字段
            rec = SessionRecord(
                id="testsession123",
                workspace="/tmp/ws",
                project_id="proj_test_nonempty",
            )
            db.add(rec)
            await db.commit()

    asyncio.run(insert())

    r = client.delete("/api/projects/proj_test_nonempty")
    assert r.status_code == 409
    assert "1" in r.json()["detail"]


def test_delete_nonempty_project_with_force(app_and_client):
    """非空 project 带 force 时 SET NULL session + 删 project。"""
    from operon.db.schema import Project, SessionRecord

    app, client, engine = app_and_client
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with factory() as db:
            proj = Project(id="proj_test_force", name="test")
            db.add(proj)
            await db.commit()
            rec = SessionRecord(
                id="testsessforc",
                workspace="/tmp/ws",
                project_id="proj_test_force",
            )
            db.add(rec)
            await db.commit()

    asyncio.run(setup())

    r = client.delete("/api/projects/proj_test_force?force=true")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert r.json()["sessions_detached"] == 1


def test_get_project_detail(app_and_client):
    """GET /api/projects/{pid} 详情含 sessions 列表。"""
    _, client, _ = app_and_client
    r = client.get("/api/projects/proj_default")
    assert r.status_code == 200
    detail = r.json()
    assert detail["id"] == "proj_default"
    assert "sessions" in detail
    assert isinstance(detail["sessions"], list)


def test_get_nonexistent_project_detail_404(app_and_client):
    _, client, _ = app_and_client
    r = client.get("/api/projects/proj_bogus")
    assert r.status_code == 404


def test_list_projects_ordered_default_first(app_and_client):
    """GET /api/projects 排序: 默认 project 在前。"""
    _, client, _ = app_and_client
    client.post("/api/projects", json={"name": "AAA"})
    client.post("/api/projects", json={"name": "BBB"})
    r = client.get("/api/projects")
    projects = r.json()
    # 默认 project 排首位
    assert projects[0]["id"] == "proj_default"
