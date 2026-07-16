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

from operon.config import load_settings
from operon.llm.openai_compat import OpenAICompatClient
from operon.settings import (
    AppSettings,
    SettingsStore,
    get_app_settings,
    mask_app_settings,
    unmask_app_settings,
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


class RunReq(BaseModel):
    prompt: str
    plan_mode: bool | None = None  # 覆盖会话级 plan_mode
    deep_review: bool | None = None  # 覆盖会话级 deep_review (深度综述模式)


def create_app() -> FastAPI:
    # SessionManager 在此处创建 (端点闭包捕获它), lifespan 启动时把 DB factory
    # 注入进去 (SessionManager 持有可变 db_session_factory 字段)。
    manager = SessionManager()

    # lifespan: 启动时初始化 SQLite engine + 注入 SessionManager (阶段 4 落库)
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = load_settings()
        app.state.settings = settings
        try:
            from operon.db.session import init_engine, session_factory

            engine = await init_engine(settings.db_url())
            db_factory = session_factory(engine)
            manager.db_session_factory = db_factory
            app.state.db_engine = engine
            app.state.db_factory = db_factory
            logger.info("operon-py DB ready: %s", settings.db_url())
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

    app = FastAPI(title="operon-py API", version="0.0.1", lifespan=lifespan)
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
        return {"status": "ok", "version": "0.0.2"}

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
        """列出所有已安装的 skills (内置 + 工作区),标注启用/禁用状态。"""
        from operon.skills.catalog import load_builtin_skills
        from operon.skills.parser import parse_skill_md

        settings = getattr(app.state, "settings", None) or load_settings()
        app_cfg = get_app_settings(settings.data_dir)
        disabled = set(app_cfg.disabled_skills or [])

        skills: list[dict[str, Any]] = []
        seen: set[str] = set()

        # 内置 skills
        for s in load_builtin_skills():
            if s.name in seen:
                continue
            seen.add(s.name)
            skills.append({
                "name": s.name,
                "description": s.description,
                "source": "anthropic",
                "enabled": s.name not in disabled,
            })

        # 工作区 skills
        ws_root = settings.data_dir / "workspaces"
        if ws_root.exists():
            # 只扫当前活跃会话的 workspace 里的 .claude/skills
            # 以及一个全局的 data_dir/.claude/skills (如果有)
            for base in [settings.data_dir / ".claude" / "skills"]:
                if not base.exists():
                    continue
                for child in sorted(base.iterdir()):
                    if not child.is_dir() or child.name.startswith("."):
                        continue
                    skill_md = child / "SKILL.md"
                    if not skill_md.exists():
                        continue
                    try:
                        content = skill_md.read_text(encoding="utf-8")
                        s = parse_skill_md(content, base_dir=child, source="local")
                        if s.name in seen:
                            continue
                        seen.add(s.name)
                        skills.append({
                            "name": s.name,
                            "description": s.description,
                            "source": "local",
                            "enabled": s.name not in disabled,
                        })
                    except Exception:
                        pass

        return skills

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

        base_url = req.base_url or (tier.base_url if tier else None)
        api_key = req.api_key or (tier.api_key if tier else None)
        model = req.model or (tier.model if tier else None)
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

        client = OpenAICompatClient(base_url=base_url, api_key=api_key, model=model)

        # MCP: 请求体优先, 否则用 config.toml 的
        mcp_raw = req.mcp_servers if req.mcp_servers is not None else settings.mcp_servers
        mcp_servers = None
        if mcp_raw:
            mcp_servers = [
                MCPServerConfig(
                    name=s["name"],
                    url=s["url"],
                    headers=s.get("headers", {}),
                )
                for s in mcp_raw
            ]

        # api_keys: 请求体与 config.toml 合并 (请求体优先)
        merged_keys: dict[str, str] = {}
        merged_keys.update({k: v for k, v in settings.api_keys.items() if v})
        if req.api_keys:
            merged_keys.update(req.api_keys)

        # disabled_skills: 请求体优先, 否则从 settings.json 读
        disabled_skills = req.disabled_skills
        if disabled_skills is None:
            app_cfg = get_app_settings(settings.data_dir)
            disabled_skills = app_cfg.disabled_skills or None

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
            raise HTTPException(403, "path outside workspace")
        if not p.exists() or not p.is_file():
            raise HTTPException(404, "file not found")
        content = p.read_bytes()
        # 文本文件直接返回内容; 二进制 (PNG/PDF) 返回 bytes
        text_exts = {".tex", ".md", ".txt", ".py", ".csv", ".json", ".bib", ".sty", ".cls"}
        if p.suffix.lower() in text_exts and not download:
            return PlainTextResponse(p.read_text(encoding="utf-8", errors="replace"))
        # 否则作为附件下载
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
            raise HTTPException(403, "path outside workspace")
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
        active = manager.get(sid)
        if active is None:
            raise HTTPException(404, "session not found")
        result = await manager.run(sid, req.prompt)
        return {
            "kind": result.kind.value,
            "final_text": result.final_text,
            "awaiting": result.awaiting,
            "error": result.error,
            "usage": result.usage,
            "iterations": result.iterations,
        }

    # ---- 批准 plan ----
    @app.post("/api/sessions/{sid}/approve")
    async def approve_plan(sid: str) -> dict[str, Any]:
        if manager.get(sid) is None:
            raise HTTPException(404, "session not found")
        return await manager.approve_plan(sid)

    # ---- 流式运行 (WebSocket) ----
    @app.websocket("/api/sessions/{sid}/stream")
    async def stream(ws: WebSocket, sid: str) -> None:
        active = manager.get(sid)
        if active is None:
            await ws.accept()
            await ws.send_json({"type": "error", "message": "session not found"})
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

        active = manager.get(sid)
        if active is None:
            raise HTTPException(404, "session not found")
        if not req.prompt:
            raise HTTPException(400, "no prompt")
        queue = active.callbacks.queue

        async def event_gen():
            run_task = asyncio.create_task(manager.run(sid, req.prompt, plan_mode=req.plan_mode, deep_review=req.deep_review))
            try:
                while True:
                    event = await queue.get()
                    yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("type") == "complete":
                        break
                # 确保 run 正常结束 (异常会在此抛出, 被外层捕获)
                await run_task
            except asyncio.CancelledError:
                run_task.cancel()
                raise
            except Exception as e:
                yield f"data: {_json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'}, ensure_ascii=False)}\n\n"
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
