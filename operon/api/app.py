"""FastAPI 应用 — HTTP/WebSocket API 层。


本项目: FastAPI + WebSocket,暴露 agent 能力给前端。

端点:
- GET  /api/health        健康检查
- GET  /api/sessions      列会话
- POST /api/sessions      创建会话 {base_url, api_key, model, workspace, plan_mode}
- GET  /api/sessions/{id} 会话状态快照
- POST /api/sessions/{id}/run       启动运行 {prompt} (非流式,返回结果)
- POST /api/sessions/{id}/approve   批准 plan
- WS   /api/sessions/{id}/stream    流式运行 {prompt} (事件实时推)

LLM 凭据: 创建会话时传入 (前端持有),不存服务端。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import operon
from operon.config import load_settings
from operon.llm.openai_compat import OpenAICompatClient
from operon.settings import (
    AppSettings,
    SettingsStore,
    get_app_settings,
    mask_app_settings,
    unmask_app_settings,
    unmask_from_candidates,
)

from .sessions import SessionManager

logger = logging.getLogger(__name__)


# ---- 请求模型 (模块级,FastAPI 才能正确解析为 body) ----


class CreateSession(BaseModel):
    # 全部可选: 缺省时从 config.toml 兜底 (配好 config.toml 后前端无需手填)
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    workspace: str | None = None
    plan_mode: bool = False
    max_iterations: int = 40
    # 模型上下文长度 (token),决定 Rolling Compact 触发阈值。
    # 必须匹配真实模型 (如 step-3.7-flash=256000),否则 Compact 会在爆窗后才触发。
    context_window: int | None = None
    # MCP servers: [{name, url, headers:{Authorization:"Bearer ..."}}]
    mcp_servers: list[dict] | None = None
    # 数据源 API keys: {"OPENALEX_API_KEY": "..."}
    api_keys: dict[str, str] | None = None
    # 被用户禁用的 skill 名称列表 (从设置面板传入)
    disabled_skills: list[str] | None = None
    # 论文模板 id (见 GET /api/templates)。默认 article。
    # 会话创建时把该模板的 template.tex 复制进工作区作为 main.tex。
    template: str | None = None
    # Layer A.5: 所属 project id。不传或空时用 'proj_default'。
    project_id: str | None = None


# ---- 请求模型 (模块级, FastAPI 才能正确解析为 body) - Layer A.5 Project ----


class CreateProject(BaseModel):
    name: str
    description: str | None = None


class UpdateProject(BaseModel):
    name: str | None = None
    description: str | None = None
    last_session_id: str | None = None


class RunReq(BaseModel):
    prompt: str
    plan_mode: bool | None = None  # 覆盖会话级 plan_mode
    deep_review: bool | None = None  # 覆盖会话级 deep_review (深度综述模式)


class CompileReq(BaseModel):
    path: str  # .tex 相对工作区的路径 (如 "main.tex")
    out_name: str | None = None


def create_app() -> FastAPI:
    # SessionManager 在此处创建 (端点闭包捕获它), lifespan 启动时把 DB factory
    # 注入进去 (SessionManager 持有可变 db_session_factory 字段)。
    manager = SessionManager()

    # lifespan: 启动时初始化 SQLite engine + 注入 SessionManager (阶段 4 落库)
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = load_settings()
        app.state.settings = settings
        # 统一日志配置 (原本项目零 handler 配置, info/debug 默认不输出)
        try:
            from operon.observability import setup_logging
            from operon.observability.trace import cleanup_old_traces

            setup_logging(log_dir=settings.data_dir_resolved() / "logs")
            cleanup_old_traces(settings.data_dir_resolved(), settings.trace.retention_days)
        except Exception as e:
            logger.warning("logging setup failed: %s", e)
        try:
            from operon.db.session import init_engine, session_factory

            engine = await init_engine(settings.db_url())
            db_factory = session_factory(engine)
            manager.db_session_factory = db_factory
            app.state.db_engine = engine
            app.state.db_factory = db_factory
            logger.info("axiom-core DB ready: %s", settings.db_url())
        except Exception as e:
            # DB 初始化失败不阻断 API 启动 (退化为纯内存 ArtifactStore)
            logger.warning("DB init failed, falling back to in-memory: %s", e)
        app.state.manager = manager
        yield
        # 关闭 engine
        engine = getattr(app.state, "db_engine", None)
        if engine is not None:
            try:
                await engine.dispose()
            except Exception:
                pass

    app = FastAPI(title="axiom-core API", version=operon.__version__, lifespan=lifespan)
    # 允许前端跨域 (开发时前端在 5173,后端在 8000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- 健康检查 ----
    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": operon.__version__}

    # ---- 配置摘要 (前端据此判断后端是否已配好 LLM, 跳过手填弹窗) ----
    @app.get("/api/config")
    async def config_summary() -> dict[str, Any]:
        settings = getattr(app.state, "settings", None) or load_settings()
        tier = settings.models.tier(settings.default_model_tier) if settings.models else None
        return {
            # 只回传"是否已配"+非敏感字段; key 永不回传
            "llm_configured": bool(
                tier and tier.api_key
            ),
            "model": tier.model if tier else None,
            "base_url": tier.base_url if tier else None,
            "context_window": tier.context_window if tier else None,
            "has_mcp": bool(settings.mcp_servers),
            "mcp_count": len(settings.mcp_servers),
            "api_keys_configured": sorted(settings.api_keys.keys()),  # 只回 key 名, 不回值
            "default_workspace": str(settings.data_dir / "workspaces"),
            "host": settings.host,
            "port": settings.port,
        }

    # ---- APP 内设置持久化 ----
    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        settings = getattr(app.state, "settings", None) or load_settings()
        app_cfg = get_app_settings(settings.data_dir)
        masked = mask_app_settings(app_cfg)
        return masked.model_dump(mode="json")

    @app.post("/api/settings")
    async def save_settings(body: dict[str, Any]) -> dict[str, Any]:
        settings = getattr(app.state, "settings", None) or load_settings()
        store = SettingsStore(settings.data_dir)
        existing = store.load() or AppSettings()
        try:
            incoming = AppSettings(**body)
        except Exception as e:
            raise HTTPException(400, f"invalid settings: {e}") from e
        merged = unmask_app_settings(incoming, existing)
        if not any(p.enabled for p in merged.llm_providers):
            raise HTTPException(400, "至少需要一个启用的 LLM Provider")
        store.save(merged)
        # 重新加载后端配置
        app.state.settings = load_settings()
        return {"status": "saved"}

    async def _get_workspace(mgr: SessionManager, sid: str) -> Path | None:
        """获取会话的 workspace 路径: 先查内存, 再查 DB。"""
        active = mgr.get(sid)
        if active is not None:
            return active.ctx.workspace
        # 会话不在内存中 (已切换/重启), 从 DB 读 workspace
        db_sessions = await mgr.list_sessions()
        rec = next((s for s in db_sessions if s["id"] == sid), None)
        if rec and rec.get("workspace"):
            return Path(rec["workspace"])
        return None

    # ---- 记忆管理 ----
    @app.get("/api/memories")
    async def list_memories(entity: str | None = None) -> list[dict[str, Any]]:
        """列出记忆 (可按 entity 过滤)。"""
        from operon.memory.store import MemoryStore

        store = MemoryStore(db_session_factory=getattr(app.state, "db_factory", None))
        if entity:
            return await store.list_by_entity(entity)
        return await store.list_all()

    @app.delete("/api/memories/{mem_id}")
    async def delete_memory(mem_id: str) -> dict[str, str]:
        """删除一条记忆。"""
        from operon.memory.store import MemoryStore

        store = MemoryStore(db_session_factory=getattr(app.state, "db_factory", None))
        ok = await store.remove(mem_id)
        return {"status": "deleted" if ok else "not_found"}

    # ---- Skills 列表 ----
    @app.get("/api/skills")
    async def list_skills() -> list[dict[str, Any]]:
        """列出全局/可共享的 skills (内置/全局/Claude/自定义),标注启用/禁用状态。

        项目级 skills (workspace/.axiom/skills) 与会话 workspace 绑定,
        在会话启动时加载,不在这里列出。
        """
        from operon.skills.catalog import (
            load_builtin_skills,
            load_claude_skills,
            load_custom_skills,
            load_global_skills,
        )

        settings = getattr(app.state, "settings", None) or load_settings()
        app_cfg = get_app_settings(settings.data_dir)
        disabled = set(app_cfg.disabled_skills or [])

        skills: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _add(source: str, items: list) -> None:
            for s in items:
                if s.name in seen:
                    continue
                seen.add(s.name)
                skills.append({
                    "name": s.name,
                    "description": s.description,
                    "source": source,
                    "enabled": s.name not in disabled,
                })

        # 1. 内置 skills (工具级,随安装包只读)
        _add("anthropic", load_builtin_skills())
        # 2. 全局自定义 skills
        _add("global", load_global_skills(settings.data_dir))
        # 3. Claude Code 用户级 skills
        if app_cfg.load_claude_skills:
            _add("claude", load_claude_skills())
        # 4. 自定义目录
        _add("custom", load_custom_skills(app_cfg.skill_extra_dirs))

        return skills

    # ---- 论文模板 ----
    @app.get("/api/templates")
    async def list_templates() -> list[dict[str, Any]]:
        """列出可用论文模板 (内置 + 用户自定义)。

        每个模板含 id/name/description/documentclass/columns。
        用户自定义模板放 {data_dir}/templates/{id}/ 下。
        """
        from operon.templates import list_templates as _list_templates

        settings = getattr(app.state, "settings", None) or load_settings()
        return [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "documentclass": t.documentclass,
                "columns": t.columns,
            }
            for t in _list_templates(settings.data_dir)
        ]

    @app.get("/api/templates/{template_id}")
    async def get_template(template_id: str) -> dict[str, Any]:
        """返回某模板的 template.tex 内容 (供前端预览/复制)。"""
        from fastapi.responses import PlainTextResponse

        from operon.templates import get_template_path

        settings = getattr(app.state, "settings", None) or load_settings()
        path = get_template_path(template_id, settings.data_dir)
        if path is None:
            raise HTTPException(404, f"template '{template_id}' not found")
        return PlainTextResponse(path.read_text(encoding="utf-8"))

    # ---- MCP 单 server 工具探测 ----
    @app.get("/api/mcp/{server_name}/tools")
    async def mcp_server_tools(server_name: str) -> dict[str, Any]:
        """临时连接一个 MCP server, 返回其工具列表, 然后断开。
        用于设置面板中展开查看 server 提供的工具。"""
        settings = getattr(app.state, "settings", None) or load_settings()
        app_cfg = get_app_settings(settings.data_dir)

        # 从 settings 找到这个 server
        server = next((s for s in app_cfg.mcp_servers if s.name == server_name), None)
        if server is None:
            raise HTTPException(404, f"MCP server '{server_name}' not found in settings")
        if not server.url:
            raise HTTPException(400, f"MCP server '{server_name}' has no URL")

        from operon.mcp.client import MCPClient

        client = MCPClient(server.url, headers=server.headers or None)
        try:
            await client.connect()
            tools = [
                {"name": t.get("name", ""), "description": t.get("description", "")}
                for t in client.tools
            ]
            await client.close()
            return {
                "name": server_name,
                "url": server.url,
                "enabled": server.enabled,
                "connected": True,
                "tools": tools,
            }
        except Exception as e:
            try:
                await client.close()
            except Exception:
                pass
            return {
                "name": server_name,
                "url": server.url,
                "enabled": server.enabled,
                "connected": False,
                "tools": [],
                "error": str(e),
            }

    # ---- 项目 CRUD (Layer A.5) ----
    from operon.db.session import DEFAULT_PROJECT_ID

    def _get_db_factory():
        return getattr(app.state, "db_factory", None)

    @app.get("/api/projects")
    async def list_projects(archived: bool = False) -> list[dict[str, Any]]:
        """列出所有 project, 含 session 数 + 最后活动时间。

        archived=false (默认): 只返回未归档 project (活跃下拉用)
        archived=true: 只返回已归档 project (归档管理弹窗用)
        """
        db_factory = _get_db_factory()
        if db_factory is None:
            return []
        from sqlalchemy import case, func, select

        from operon.db.schema import Project, SessionRecord

        async with db_factory() as db:
            # 每个 project 的 session 数 + 最近 updated_at
            stmt = (
                select(
                    Project,
                    func.count(SessionRecord.id).label("session_count"),
                    func.max(SessionRecord.updated_at).label("last_activity"),
                )
                .outerjoin(SessionRecord, SessionRecord.project_id == Project.id)
                .where(Project.archived.is_(archived))
                .group_by(Project.id)
                # 默认 project 排首位 (CASE 把默认 id 映射为 0, 其他为 1, 升序排)
                .order_by(
                    case((Project.id == DEFAULT_PROJECT_ID, 0), else_=1),
                    Project.updated_at.desc(),
                )
            )
            result = await db.execute(stmt)
            rows = result.all()
            return [
                {
                    "id": proj.id,
                    "name": proj.name,
                    "description": proj.description,
                    "last_session_id": proj.last_session_id,
                    "session_count": sess_count,
                    "last_activity_at": last_act.isoformat() if last_act else None,
                    "created_at": proj.created_at.isoformat() if proj.created_at else None,
                    "is_default": proj.id == DEFAULT_PROJECT_ID,
                    "archived": bool(proj.archived),
                }
                for proj, sess_count, last_act in rows
            ]

    @app.post("/api/projects")
    async def create_project(req: CreateProject) -> dict[str, Any]:
        """创建 project。返回新 project 信息。"""
        import secrets

        from operon.db.schema import Project

        db_factory = _get_db_factory()
        if db_factory is None:
            return {"error": "DB not available"}
        pid = f"proj_{secrets.token_hex(4)}"
        async with db_factory() as db:
            proj = Project(id=pid, name=req.name, description=req.description)
            db.add(proj)
            await db.commit()
            return {
                "id": proj.id,
                "name": proj.name,
                "description": proj.description,
                "last_session_id": None,
                "session_count": 0,
                "is_default": False,
                "archived": False,
            }

    @app.patch("/api/projects/{pid}")
    async def update_project(pid: str, req: UpdateProject) -> dict[str, Any]:
        """改名/改描述/更新 last_session_id。"""
        db_factory = _get_db_factory()
        if db_factory is None:
            return {"error": "DB not available"}
        from sqlalchemy import select

        from operon.db.schema import Project

        async with db_factory() as db:
            result = await db.execute(select(Project).where(Project.id == pid))
            proj = result.scalar_one_or_none()
            if proj is None:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail=f"project {pid} not found")
            if req.name is not None:
                proj.name = req.name
            if req.description is not None:
                proj.description = req.description
            if req.last_session_id is not None:
                proj.last_session_id = req.last_session_id
            await db.commit()
            return {
                "id": proj.id,
                "name": proj.name,
                "description": proj.description,
                "last_session_id": proj.last_session_id,
                "is_default": proj.id == DEFAULT_PROJECT_ID,
                "archived": bool(proj.archived),
            }

    @app.post("/api/projects/{pid}/archive")
    async def archive_project(pid: str) -> dict[str, Any]:
        """归档 project (软删除): 从活跃下拉隐藏, 可恢复。

        默认 project 不允许归档。返回 project 最新状态。
        """
        from fastapi import HTTPException
        from sqlalchemy import select

        from operon.db.schema import Project

        if pid == DEFAULT_PROJECT_ID:
            raise HTTPException(
                status_code=400, detail="cannot archive default project"
            )
        db_factory = _get_db_factory()
        if db_factory is None:
            return {"error": "DB not available"}
        async with db_factory() as db:
            result = await db.execute(select(Project).where(Project.id == pid))
            proj = result.scalar_one_or_none()
            if proj is None:
                raise HTTPException(status_code=404, detail=f"project {pid} not found")
            proj.archived = True
            await db.commit()
            return {
                "id": proj.id,
                "name": proj.name,
                "archived": True,
            }

    @app.post("/api/projects/{pid}/unarchive")
    async def unarchive_project(pid: str) -> dict[str, Any]:
        """恢复归档的 project: 重新出现在活跃下拉。"""
        from fastapi import HTTPException
        from sqlalchemy import select

        from operon.db.schema import Project

        db_factory = _get_db_factory()
        if db_factory is None:
            return {"error": "DB not available"}
        async with db_factory() as db:
            result = await db.execute(select(Project).where(Project.id == pid))
            proj = result.scalar_one_or_none()
            if proj is None:
                raise HTTPException(status_code=404, detail=f"project {pid} not found")
            proj.archived = False
            await db.commit()
            return {
                "id": proj.id,
                "name": proj.name,
                "archived": False,
            }

    @app.delete("/api/projects/{pid}")
    async def delete_project(pid: str, force: bool = False) -> dict[str, Any]:
        """永久删除 project (不可恢复)。

        常规流程是先归档 (POST /archive), 用户在归档弹窗里确认后永久删除。
        force=false (默认): 如果还有 session, 返回 409。
        force=true: 把所属 session 的 project_id SET NULL, 删除 project 行。
        默认 project (proj_default) 不允许删除。
        """
        from fastapi import HTTPException
        from sqlalchemy import func, select

        from operon.db.schema import Project, SessionRecord

        if pid == DEFAULT_PROJECT_ID:
            raise HTTPException(status_code=400, detail="cannot delete default project")

        db_factory = _get_db_factory()
        if db_factory is None:
            return {"error": "DB not available"}
        async with db_factory() as db:
            result = await db.execute(select(Project).where(Project.id == pid))
            proj = result.scalar_one_or_none()
            if proj is None:
                raise HTTPException(status_code=404, detail=f"project {pid} not found")

            count_result = await db.execute(
                select(func.count(SessionRecord.id)).where(SessionRecord.project_id == pid)
            )
            sess_count = count_result.scalar() or 0
            if sess_count > 0 and not force:
                raise HTTPException(
                    status_code=409,
                    detail=f"project has {sess_count} sessions; use ?force=true to detach them",
                )

            # detach sessions (SET NULL)
            if sess_count > 0:
                from sqlalchemy import update

                await db.execute(
                    update(SessionRecord)
                    .where(SessionRecord.project_id == pid)
                    .values(project_id=None)
                )
            await db.delete(proj)
            await db.commit()
            return {
                "id": pid,
                "deleted": True,
                "sessions_detached": sess_count,
            }

    @app.get("/api/projects/{pid}")
    async def get_project(pid: str) -> dict[str, Any]:
        """project 详情, 含 sessions 列表。"""
        from fastapi import HTTPException
        from sqlalchemy import select

        from operon.db.schema import Project, SessionRecord

        db_factory = _get_db_factory()
        if db_factory is None:
            return {"error": "DB not available"}
        async with db_factory() as db:
            result = await db.execute(select(Project).where(Project.id == pid))
            proj = result.scalar_one_or_none()
            if proj is None:
                raise HTTPException(status_code=404, detail=f"project {pid} not found")
            sess_result = await db.execute(
                select(SessionRecord)
                .where(SessionRecord.project_id == pid)
                .order_by(SessionRecord.updated_at.desc())
            )
            sessions = sess_result.scalars().all()
            return {
                "id": proj.id,
                "name": proj.name,
                "description": proj.description,
                "last_session_id": proj.last_session_id,
                "is_default": proj.id == DEFAULT_PROJECT_ID,
                "archived": bool(proj.archived),
                "created_at": proj.created_at.isoformat() if proj.created_at else None,
                "sessions": [
                    {
                        "id": s.id,
                        "title": s.title,
                        "status": s.status,
                        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                    }
                    for s in sessions
                ],
            }

    # ---- 会话 CRUD ----
    @app.get("/api/sessions")
    async def list_sessions() -> list[dict[str, Any]]:
        return await manager.list_sessions()

    @app.post("/api/sessions")
    async def create_session(req: CreateSession) -> dict[str, Any]:
        from operon.mcp.manager import MCPServerConfig

        # config.toml 兜底: 请求体缺字段时从配置文件取 (配好 config.toml 后前端可不传任何凭据)
        settings = getattr(app.state, "settings", None) or load_settings()
        tier = settings.models.tier(settings.default_model_tier) if settings.models else None
        # settings.json 里存的是真实 key; 前端只持有脱敏值 (GET /api/settings),
        # 切换/保存后提交回来的可能是 mask 形式 (如 "sk-****…ab"), 真正使用前必须还原。
        app_cfg = get_app_settings(settings.data_dir)

        base_url = req.base_url or (tier.base_url if tier else None)
        model = req.model or (tier.model if tier else None)
        api_key = unmask_from_candidates(
            req.api_key or (tier.api_key if tier else None),
            [p.api_key for p in app_cfg.llm_providers] + ([tier.api_key] if tier else []),
        )
        if not (base_url and api_key and model):
            raise HTTPException(
                400,
                "LLM 未配置: 请在 config.toml 填 [models.large] 或前端设置里填, "
                "(base_url + api_key + model 至少要这三项)",
            )
        context_window = req.context_window or (tier.context_window if tier else None)
        # 每个会话分配独立工作区目录: data_dir/workspaces/{sid}
        # 不再接受外部指定的 workspace 路径 — 每个会话必须隔离
        import uuid

        sid = str(uuid.uuid4())[:12]
        workspace = settings.data_dir / "workspaces" / sid

        # 模板: 把选中的 template.tex 复制进工作区作为 main.tex (preamble 已就位)。
        # 优先用请求体; 否则用 settings 里的默认模板; 兜底 article。
        # 找不到模板时静默跳过 (不阻断建会话)。
        tpl_id = req.template or settings.default_template or "article"
        try:
            from operon.templates import get_template_path

            tpl_path = get_template_path(tpl_id, settings.data_dir)
            if tpl_path is not None:
                workspace.mkdir(parents=True, exist_ok=True)
                (workspace / "main.tex").write_text(
                    tpl_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
        except Exception:
            pass  # 模板复制失败不阻断建会话; agent 仍可自己写 main.tex

        client = OpenAICompatClient(base_url=base_url, api_key=api_key, model=model)

        # MCP: 请求体优先, 否则用 config.toml 的; header 值同样做脱敏还原
        stored_header_vals = [v for s in app_cfg.mcp_servers for v in s.headers.values()]
        mcp_raw = req.mcp_servers if req.mcp_servers is not None else settings.mcp_servers
        mcp_servers = None
        if mcp_raw:
            mcp_servers = [
                MCPServerConfig(
                    name=s["name"],
                    url=s["url"],
                    headers={
                        k: unmask_from_candidates(v, stored_header_vals)
                        for k, v in s.get("headers", {}).items()
                    },
                )
                for s in mcp_raw
            ]

        # api_keys: 请求体与 config.toml 合并 (请求体优先); 请求体的值做脱敏还原
        merged_keys: dict[str, str] = {}
        merged_keys.update({k: v for k, v in settings.api_keys.items() if v})
        if req.api_keys:
            merged_keys.update(
                {
                    k: unmask_from_candidates(v, [app_cfg.api_keys.get(k)])
                    for k, v in req.api_keys.items()
                }
            )

        # disabled_skills: 请求体优先, 否则从 settings.json 读
        disabled_skills = req.disabled_skills
        if disabled_skills is None:
            disabled_skills = app_cfg.disabled_skills or None

        # Layer A.5: project_id 解析 (None/空 → proj_default)
        from operon.db.session import DEFAULT_PROJECT_ID

        project_id = req.project_id or DEFAULT_PROJECT_ID

        active = await manager.create(
            llm=client,
            workspace=workspace,
            sid=sid,
            plan_mode=req.plan_mode,
            max_iterations=req.max_iterations,
            model=model,
            context_window=context_window,
            mcp_servers=mcp_servers,
            api_keys=merged_keys or None,
            disabled_skills=disabled_skills,
            data_dir=settings.data_dir,
            load_claude_skills=app_cfg.load_claude_skills,
            load_project_skills=app_cfg.load_project_skills,
            skill_extra_dirs=app_cfg.skill_extra_dirs,
            project_id=project_id,
        )
        return {
            "id": active.id,
            "frame_id": active.ctx.frame.id,
            "mcp_tools": getattr(active.session, "_mcp_tool_count", 0),
            "model": model,
            "workspace": str(workspace),
        }

    @app.get("/api/sessions/{sid}/files/{path:path}")
    async def get_file(sid: str, path: str, download: bool = False):
        """下载/读取工作区文件。供前端查看 .tex 内容 + 下载。"""
        from fastapi.responses import PlainTextResponse, Response

        ws = await _get_workspace(manager, sid)
        if ws is None:
            raise HTTPException(404, "session not found")
        p = (ws / path).resolve()
        try:
            p.relative_to(ws.resolve())
        except ValueError:
            raise HTTPException(403, "path outside workspace") from None
        if not p.exists() or not p.is_file():
            raise HTTPException(404, "file not found")
        content = p.read_bytes()
        # 文本文件直接返回内容; 二进制 (PNG/PDF) 返回 bytes
        text_exts = {".tex", ".md", ".txt", ".py", ".csv", ".json", ".bib", ".sty", ".cls"}
        if p.suffix.lower() in text_exts and not download:
            return PlainTextResponse(p.read_text(encoding="utf-8", errors="replace"))
        # PDF: 以 inline 返回, 前端用 PDF.js / <iframe> 直接渲染预览
        if p.suffix.lower() == ".pdf" and not download:
            return Response(
                content=content,
                media_type="application/pdf",
                headers={"Content-Disposition": f'inline; filename="{p.name}"'},
            )
        # 其余二进制作为附件下载
        media = "application/octet-stream"
        return Response(
            content=content,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{p.name}"'},
        )

    @app.get("/api/sessions/{sid}/files")
    async def list_files_api(sid: str) -> dict[str, Any]:
        """列出工作区所有文件 (递归,排除 .venv)。"""
        ws = await _get_workspace(manager, sid)
        if ws is None:
            raise HTTPException(404, "session not found")
        ws = ws.resolve()
        files = []
        for p in sorted(ws.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(ws)
            # 排除 venv / 隐藏缓存
            if any(part in {".venv", "__pycache__", ".git"} for part in rel.parts):
                continue
            files.append({"path": str(rel), "size": p.stat().st_size, "name": p.name})
        return {"files": files}

    @app.delete("/api/sessions/{sid}/files/{path:path}")
    async def delete_file(sid: str, path: str) -> dict[str, str]:
        """删除工作区文件 (及对应的 artifact DB 记录)。"""
        ws = await _get_workspace(manager, sid)
        if ws is None:
            raise HTTPException(404, "session not found")
        ws = ws.resolve()
        p = (ws / path).resolve()
        try:
            p.relative_to(ws)
        except ValueError:
            raise HTTPException(403, "path outside workspace") from None
        if not p.exists():
            raise HTTPException(404, "file not found")

        # 删除磁盘文件
        p.unlink()

        # 尝试删除 artifact DB 记录 (仅活跃会话有内存态 store)
        active = manager.get(sid)
        if active is not None:
            try:
                store = active.ctx.artifact_store
                filename = p.name
                for aid, rec in list(getattr(store, "_artifacts", {}).items()):
                    if rec.filename == filename:
                        del store._artifacts[aid]
                        for vid in list(getattr(store, "_versions", {}).keys()):
                            v = store._versions.get(vid)
                            if v and getattr(v, "artifact_id", None) == aid:
                                del store._versions[vid]
            except Exception as e:
                logger.warning("failed to cleanup artifact record for %s: %s", path, e)

        return {"status": "deleted"}

    @app.get("/api/sessions/{sid}")
    async def session_state(sid: str) -> dict[str, Any]:
        if manager.get(sid) is not None:
            return manager.session_state(sid)
        # 内存中不存在,尝试从 DB 加载 (历史会话只读查看)
        state = await manager.session_state_from_db(sid)
        if state is None:
            raise HTTPException(404, "session not found")
        return state

    @app.delete("/api/sessions/{sid}")
    async def delete_session(sid: str) -> dict[str, str]:
        await manager.delete_session(sid)
        return {"status": "deleted"}

    # ---- 运行 (非流式) ----
    @app.post("/api/sessions/{sid}/run")
    async def run_session(sid: str, req: RunReq) -> dict[str, Any]:
        # 若会话不在内存中 (已切换/重启), 自动从 DB 恢复
        try:
            await manager.get_or_restore(sid)
        except Exception as e:
            raise HTTPException(404, f"session not found: {e}") from e
        result = await manager.run(sid, req.prompt)
        # ask_user 时把问题/选项带上 (与 SSE complete 事件一致)
        pending_ask = None
        if result.awaiting == "user_response":
            active = manager._sessions.get(sid)
            pa = getattr(active.ctx, "pending_ask", None) if active else None
            if pa is not None:
                pending_ask = {"question": pa.question, "options": list(pa.options)}
        return {
            "kind": result.kind.value,
            "final_text": result.final_text,
            "awaiting": result.awaiting,
            "pending_ask": pending_ask,
            "error": result.error,
            "usage": result.usage,
            "iterations": result.iterations,
        }

    # ---- 批准 plan ----
    @app.post("/api/sessions/{sid}/approve")
    async def approve_plan(sid: str) -> dict[str, Any]:
        # 同样支持恢复后再审批
        try:
            await manager.get_or_restore(sid)
        except Exception as e:
            raise HTTPException(404, f"session not found: {e}") from e
        return await manager.approve_plan(sid)

    # ---- 编译 .tex → PDF (前端 PDF 预览用) ----
    @app.post("/api/sessions/{sid}/compile")
    async def compile_session(sid: str, req: CompileReq) -> dict[str, Any]:
        """在工作区用 tectonic 编译 .tex → PDF, 返回结果 + 错误详情。

        前端 PDF 预览页调用: 成功后直接拉 GET /files/{pdf} 渲染。
        失败时返回从 .log 解析出的具体错误行 (如 "第 221 行 TikZ positioning 缺失")。
        """
        ws = await _get_workspace(manager, sid)
        if ws is None:
            raise HTTPException(404, "session not found")
        from operon.tools.builtins.latex import compile_tex

        result = await compile_tex(ws, req.path, out_name=req.out_name)
        return {
            "success": result.success,
            "pdf_path": result.rel_pdf,
            "size_kb": round(result.size_kb),
            "message": result.message,
            "errors": result.errors,
            "log_excerpt": result.log_excerpt[-3000:],
        }

    # ---- 流式运行 (WebSocket) ----
    @app.websocket("/api/sessions/{sid}/stream")
    async def stream(ws: WebSocket, sid: str) -> None:
        try:
            active = await manager.get_or_restore(sid)
        except Exception as e:
            await ws.accept()
            await ws.send_json({"type": "error", "message": f"session not found: {e}"})
            await ws.close()
            return

        await ws.accept()
        try:
            # 等前端发 prompt
            init = await ws.receive_json()
            prompt = init.get("prompt", "")
            if not prompt:
                await ws.send_json({"type": "error", "message": "no prompt"})
                await ws.close()
                return

            # 启动 run 任务,同时消费事件队列推给 ws
            run_task = asyncio.create_task(manager.run(sid, prompt))

            async def _pump() -> None:
                while True:
                    event = await active.callbacks.queue.get()
                    if event.get("type") == "complete":
                        await ws.send_json(event)
                        return
                    await ws.send_json(event)

            pump_task = asyncio.create_task(_pump())
            # 等两者之一完成
            done, pending = await asyncio.wait(
                {run_task, pump_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
            await run_task  # 确保异常抛出

        except WebSocketDisconnect:
            pass
        except Exception as e:
            await ws.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    # ---- SSE 流式端点 (POST, 替代 WS; 事件模型与 WS 完全相同, 只是传输层换 SSE) ----
    @app.post("/api/sessions/{sid}/stream-sse")
    async def stream_sse(sid: str, req: RunReq):
        """SSE 流式运行。前端用 fetch + ReadableStream 收事件。

        事件格式: data: {json}\n\n (与 WS 的 send_json 同结构, 只是换传输)
        结束: yield 一个 type=complete 事件后关闭流。
        """
        import json as _json

        from fastapi.responses import StreamingResponse

        try:
            active = await manager.get_or_restore(sid)
        except Exception as e:
            raise HTTPException(404, f"session not found: {e}") from e
        if not req.prompt:
            raise HTTPException(400, "no prompt")
        queue = active.callbacks.queue

        async def event_gen():
            run_task = asyncio.create_task(
                manager.run(
                    sid, req.prompt, plan_mode=req.plan_mode, deep_review=req.deep_review
                )
            )
            logger.info("SSE stream start: session=%s", sid)
            try:
                while True:
                    event = await queue.get()
                    yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("type") == "complete":
                        break
                # 确保 run 正常结束 (异常会在此抛出, 被外层捕获)
                await run_task
                logger.info(
                    "SSE stream complete: session=%s, kind=%s",
                    sid,
                    event.get("kind"),
                )
            except asyncio.CancelledError:
                run_task.cancel()
                logger.warning("SSE stream cancelled by client: session=%s", sid)
                raise
            except Exception as e:
                logger.exception("SSE stream error: session=%s", sid)
                err = _json.dumps(
                    {"type": "error", "message": f"{type(e).__name__}: {e}"},
                    ensure_ascii=False,
                )
                yield f"data: {err}\n\n"
            finally:
                if not run_task.done():
                    run_task.cancel()

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # 禁 nginx 缓冲 (若经反代)
            },
        )

    # ---- 静态托管前端 (生产: 后端直接服务 frontend/dist) ----
    # mount 在所有 API 路由之后; /api/* 由上面的端点处理, 其余落到 SPA。
    # frontend/dist 由 vite build 产出; 不存在时跳过 (dev 模式用 vite 5173)。
    try:
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
        if dist.is_dir():
            # /assets 等静态资源直出
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

            # SPA fallback: 任意非 /api 路径返回 index.html (前端路由 /paper/<sid> 等)
            @app.get("/{full_path:path}")
            async def spa_fallback(full_path: str):
                # 静态文件优先 (如 /vite.svg)
                candidate = dist / full_path
                if full_path and candidate.is_file():
                    return FileResponse(candidate)
                # 其余一律返回 index.html, 由 React Router 接管
                return FileResponse(dist / "index.html")
    except Exception as e:
        logger.warning("frontend static mount skipped: %s", e)

    return app


app = create_app()
