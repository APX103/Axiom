"""会话管理器。


支持:
- 创建会话 (配 LLM + workspace + plan_mode) + 写 DB
- 异步运行 (事件经 callbacks 出) + 消息落库
- 继续/审批 (plan approval → resume)
- 查询状态 (内存 + DB 降级)
- 删除会话
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from operon.agent.runner import Agent, RunResult
from operon.agent.session import Session
from operon.frames.service import FrameService
from operon.llm.base import LLMClient
from operon.tools.context import ToolContext

from .callbacks import WSCallbacks

logger = logging.getLogger(__name__)


@dataclass
class ActiveSession:
    """一个活跃会话的运行时状态。"""

    id: str
    session: Session
    ctx: ToolContext
    callbacks: WSCallbacks
    frame_service: FrameService
    last_result: RunResult | None = None
    running: asyncio.Task | None = None
    mcp_manager: Any = None  # MCPServerManager, 清理时需要 close_all
    _msg_seq: int = 0  # DB 消息序号计数器

    async def cleanup(self) -> None:
        """释放会话持有的资源 (MCP 连接等)。"""
        if self.mcp_manager is not None:
            try:
                await self.mcp_manager.close_all()
            except Exception:
                logger.warning("Failed to close MCP connections for session %s", self.id)
            self.mcp_manager = None


class SessionManager:
    """会话管理器。内存态 + SQLite 持久化。"""

    MAX_ACTIVE_SESSIONS = 10  # 内存中最多保留的活跃会话 (LRU 淘汰)

    def __init__(self, db_session_factory: Any = None) -> None:
        self._sessions: dict[str, ActiveSession] = {}
        self.db_session_factory = db_session_factory

    def _evict_if_needed(self) -> None:
        """超过 MAX_ACTIVE_SESSIONS 时, 清理最旧的空闲会话 (LRU)。"""
        while len(self._sessions) > self.MAX_ACTIVE_SESSIONS:
            # 找最旧的 (非 running 的) 会话
            oldest_sid = None
            oldest_time = None
            for sid, active in self._sessions.items():
                if active.running is not None and not active.running.done():
                    continue  # 跳过正在运行的
                created = active.ctx.frame.created_at
                if oldest_time is None or created < oldest_time:
                    oldest_time = created
                    oldest_sid = sid
            if oldest_sid is None:
                break  # 全都在运行, 不淘汰
            evicted = self._sessions.pop(oldest_sid)
            logger.info("Evicting inactive session %s from memory (LRU)", oldest_sid)
            # 异步清理 (不阻塞当前操作)
            asyncio.create_task(evicted.cleanup())

    # ---- 内部 DB 辅助 ----

    async def _db_save_session(self, active: ActiveSession) -> None:
        """将会话元数据写入 sessions 表。"""
        if not self.db_session_factory:
            return
        try:
            from operon.db.schema import SessionRecord

            async with self.db_session_factory() as db:
                rec = SessionRecord(
                    id=active.id,
                    title=None,
                    workspace=str(active.session.config.workspace),
                    model=active.session.config.model,
                    plan_mode=active.session.config.plan_mode,
                    status="active",
                )
                db.add(rec)
                await db.commit()
        except Exception:
            logger.exception("Failed to persist session %s to DB", active.id)

    async def _db_save_messages(self, sid: str, messages: list[dict]) -> None:
        """批量追加消息到 session_messages 表。"""
        if not self.db_session_factory or not messages:
            return
        try:
            from operon.db.schema import SessionMessage

            active = self._sessions.get(sid)
            seq_start = active._msg_seq if active else 0

            async with self.db_session_factory() as db:
                for i, msg in enumerate(messages):
                    # harness_notice (memory 召回块/max_tokens 续传提示等) 是消息级标记,
                    # content 列没有独立列存放, 这里把它编进 content JSON, load 时还原。
                    # 这样不新增 DB 列, 旧数据 (裸 content) 也不受影响。
                    payload = msg["content"]
                    if msg.get("harness_notice"):
                        payload = {"_harness_notice": True, "content": payload}
                    rec = SessionMessage(
                        session_id=sid,
                        seq=seq_start + i,
                        role=msg["role"],
                        content=json.dumps(payload, ensure_ascii=False),
                    )
                    db.add(rec)
                await db.commit()

            if active:
                active._msg_seq = seq_start + len(messages)
        except Exception:
            logger.exception("Failed to persist messages for session %s", sid)

    async def _db_update_title(self, sid: str, title: str) -> None:
        """用首条用户消息设置 session title。"""
        if not self.db_session_factory:
            return
        try:
            from sqlalchemy import update

            from operon.db.schema import SessionRecord

            async with self.db_session_factory() as db:
                await db.execute(
                    update(SessionRecord)
                    .where(SessionRecord.id == sid, SessionRecord.title.is_(None))
                    .values(title=title[:100])
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to update title for session %s", sid)

    async def _db_list_sessions(self) -> list[dict[str, Any]]:
        """从 DB 读取所有会话记录。"""
        if not self.db_session_factory:
            return []
        try:
            from sqlalchemy import select

            from operon.db.schema import SessionRecord

            async with self.db_session_factory() as db:
                result = await db.execute(
                    select(SessionRecord).order_by(SessionRecord.updated_at.desc())
                )
                rows = result.scalars().all()
                return [
                    {
                        "id": r.id,
                        "title": r.title,
                        "workspace": r.workspace,
                        "model": r.model,
                        "plan_mode": r.plan_mode,
                        "status": r.status,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                    }
                    for r in rows
                ]
        except Exception:
            logger.exception("Failed to list sessions from DB")
            return []

    async def _db_load_messages(self, sid: str) -> list[dict[str, Any]]:
        """从 DB 加载某会话的所有消息。"""
        if not self.db_session_factory:
            return []
        try:
            from sqlalchemy import select

            from operon.db.schema import SessionMessage

            async with self.db_session_factory() as db:
                result = await db.execute(
                    select(SessionMessage)
                    .where(SessionMessage.session_id == sid)
                    .order_by(SessionMessage.seq)
                )
                rows = result.scalars().all()
                out = []
                for r in rows:
                    payload = json.loads(r.content)
                    # 还原 harness_notice 标记 (save 时编进了 content JSON)。
                    # 旧数据是裸 content (str/list), 无 _harness_notice 键, 原样返回。
                    if isinstance(payload, dict) and payload.get("_harness_notice"):
                        out.append({
                            "role": r.role,
                            "content": payload["content"],
                            "harness_notice": True,
                        })
                    else:
                        out.append({"role": r.role, "content": payload})
                return out
        except Exception:
            logger.exception("Failed to load messages for session %s", sid)
            return []

    async def _db_delete_session(self, sid: str) -> None:
        """从 DB 删除会话及其消息 (级联)。"""
        if not self.db_session_factory:
            return
        try:
            from sqlalchemy import delete

            from operon.db.schema import SessionRecord

            async with self.db_session_factory() as db:
                await db.execute(delete(SessionRecord).where(SessionRecord.id == sid))
                await db.commit()
        except Exception:
            logger.exception("Failed to delete session %s from DB", sid)

    async def _db_touch_session(self, sid: str) -> None:
        """更新 session 的 updated_at。"""
        if not self.db_session_factory:
            return
        try:
            from sqlalchemy import update

            from operon.db.schema import SessionRecord, _now

            async with self.db_session_factory() as db:
                await db.execute(
                    update(SessionRecord)
                    .where(SessionRecord.id == sid)
                    .values(updated_at=_now())
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to touch session %s", sid)

    # ---- 公开 API ----

    async def create(
        self,
        *,
        llm: LLMClient,
        workspace: Path,
        sid: str | None = None,
        plan_mode: bool = False,
        max_iterations: int = 40,
        model: str | None = None,
        context_window: int | None = None,
        mcp_servers: list = None,
        api_keys: dict[str, str] | None = None,
        disabled_skills: list[str] | None = None,
    ) -> ActiveSession:
        """创建会话 (初始化 MCP + ctx,不启动 run) + 写 DB。

        Args:
            sid: 可选的 session id。传入时直接使用 (app 层需提前生成以计算 workspace 路径);
                 不传则在此处生成。
        """
        import uuid

        from operon.agent.session import SessionConfig

        if sid is None:
            sid = str(uuid.uuid4())[:12]
        callbacks = WSCallbacks()
        session = Session(
            llm=llm,
            config=SessionConfig(
                workspace=workspace,
                plan_mode=plan_mode,
                max_iterations=max_iterations,
                model=model,
                context_window=context_window,
                mcp_servers=mcp_servers,
                api_keys=api_keys or {},
                disabled_skills=disabled_skills or [],
                db_session_factory=self.db_session_factory,
            ),
            callbacks=callbacks,
        )
        ctx = await session.prepare()
        active = ActiveSession(
            id=sid,
            session=session,
            ctx=ctx,
            callbacks=callbacks,
            frame_service=session.frame_service,
            mcp_manager=session.mcp_manager,
        )
        self._sessions[sid] = active
        self._evict_if_needed()
        await self._db_save_session(active)
        return active

    def get(self, sid: str) -> ActiveSession | None:
        return self._sessions.get(sid)

    async def list_sessions(self) -> list[dict[str, Any]]:
        """列出所有会话 (DB 为准,标注内存中 active 的)。"""
        db_sessions = await self._db_list_sessions()
        if not db_sessions:
            # DB 为空或不可用,降级到内存
            out = []
            for s in self._sessions.values():
                out.append(
                    {
                        "id": s.id,
                        "title": s.ctx.frame.task_summary,
                        "status": "active",
                        "workspace": str(s.session.config.workspace),
                        "model": s.session.config.model,
                        "plan_mode": s.session.config.plan_mode,
                        "created_at": s.ctx.frame.created_at.isoformat(),
                        "updated_at": s.ctx.frame.updated_at.isoformat(),
                        "live": True,
                    }
                )
            return out

        active_ids = set(self._sessions.keys())
        for s in db_sessions:
            s["live"] = s["id"] in active_ids
        return db_sessions

    async def run(self, sid: str, prompt: str, *, plan_mode: bool | None = None) -> RunResult:
        """启动 (或继续) 一个会话。事件经 callbacks.queue 流出。消息写 DB。

        Args:
            plan_mode: 覆盖会话级的 plan_mode 设置。None=用会话配置。
        """
        active = self._sessions[sid]

        msg_count_before = len(active.ctx.frame.messages)

        from operon.tools.router import ToolRouter

        router = ToolRouter(active.session.registry)
        agent = Agent(
            llm=active.session.llm,
            tool_router=router,
            frame_service=active.frame_service,
            frame=active.ctx.frame,
            ctx=active.ctx,
            max_iterations=active.session.config.max_iterations,
            model=active.session.config.model,
            max_tokens=active.session.config.max_tokens,
            plan_mode=plan_mode if plan_mode is not None else active.session.config.plan_mode,
            callbacks=active.callbacks,
        )
        result = await agent.run(prompt)
        active.last_result = result
        await active.callbacks.emit_complete(result, active.ctx)

        # 持久化本轮新增消息
        new_msgs = active.ctx.frame.messages[msg_count_before:]
        if new_msgs:
            serialized = [
                {
                    "role": m.role.value,
                    "content": _serialize_content(m.content),
                    "harness_notice": getattr(m, "_harness_notice", False) or None,
                }
                for m in new_msgs
            ]
            await self._db_save_messages(sid, serialized)

            # 首轮: 用首条用户消息设置 title
            if msg_count_before == 0:
                await self._db_update_title(sid, prompt)

        await self._db_touch_session(sid)
        return result

    async def approve_plan(self, sid: str) -> dict[str, Any]:
        """批准 plan (从 awaiting_plan_approval 恢复)。"""
        from operon.tools.builtins import plan as plan_tools

        active = self._sessions[sid]
        await plan_tools.approve_plan(active.ctx)
        return {"approved": True, "steps": active.ctx.plan.steps}

    def session_state(self, sid: str) -> dict[str, Any]:
        """会话状态快照 (供前端初始化 UI — 内存中的活跃会话)。"""
        active = self._sessions[sid]
        f = active.ctx.frame
        return {
            "id": active.id,
            "frame_id": f.id,
            "status": f.status.value,
            "task_summary": f.task_summary,
            "plan_mode": active.session.config.plan_mode,
            "plan": {"steps": active.ctx.plan.steps, "approved": active.ctx.plan.approved}
            if active.ctx.plan.steps
            else None,
            "artifacts": active.ctx.artifacts,
            "messages": [
                {
                    "role": m.role.value,
                    "content": _serialize_content(m.content),
                    "harness_notice": getattr(m, "_harness_notice", False) or None,
                }
                for m in f.messages
            ],
        }

    async def session_state_from_db(self, sid: str) -> dict[str, Any] | None:
        """从 DB 加载已归档会话的状态 (只读,无 runtime)。"""
        db_sessions = await self._db_list_sessions()
        rec = next((s for s in db_sessions if s["id"] == sid), None)
        if rec is None:
            return None
        messages = await self._db_load_messages(sid)
        return {
            "id": sid,
            "frame_id": None,
            "status": rec.get("status", "archived"),
            "task_summary": rec.get("title"),
            "plan_mode": rec.get("plan_mode", False),
            "plan": None,
            "artifacts": {},
            "messages": messages,
        }

    async def delete_session(self, sid: str) -> None:
        """删除会话 (内存 + DB + 工作区目录 + MCP 连接)。"""
        active = self._sessions.pop(sid, None)

        # 清理 MCP 连接 + frame 记忆
        if active is not None:
            await active.cleanup()
            # 清除该会话的 frame 记忆
            if active.ctx.memory_store is not None:
                try:
                    await active.ctx.memory_store.clear_frame(active.ctx.frame.id)
                except Exception:
                    pass

        # 先拿到 workspace 路径 (DB 删除后就查不到了)
        workspace_path = None
        if active is not None:
            workspace_path = active.session.config.workspace
        else:
            # 会话不在内存中 (已归档/重启后),从 DB 查 workspace
            db_sessions = await self._db_list_sessions()
            rec = next((s for s in db_sessions if s["id"] == sid), None)
            if rec is not None:
                workspace_path = Path(rec["workspace"]) if rec.get("workspace") else None

        await self._db_delete_session(sid)

        # 清理会话专属工作区目录。
        # 安全检查: 只删形如 .../workspaces/{sid} 的路径,
        # 绝不删用户通过 req.workspace 显式指定的外部目录。
        if workspace_path is not None:
            import shutil

            try:
                ws = Path(workspace_path).resolve()
                if ws.name == sid and ws.parent.name == "workspaces":
                    shutil.rmtree(ws, ignore_errors=True)
                    logger.info("Removed workspace dir for session %s: %s", sid, ws)
            except Exception:
                logger.exception("Failed to remove workspace dir for session %s", sid)


def _serialize_content(content: Any) -> Any:
    """把消息 content 序列化为前端可用的结构。"""
    if isinstance(content, str):
        return content
    out = []
    for b in content:
        if hasattr(b, "thinking"):  # ThinkingBlock (扩展思考,需持久化/回显)
            out.append({"type": "thinking", "thinking": b.thinking})
        elif hasattr(b, "text"):
            out.append({"type": "text", "text": b.text})
        elif hasattr(b, "name"):  # ToolUseBlock
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif hasattr(b, "tool_use_id"):  # ToolResultBlock
            out.append(
                {
                    "type": "tool_result",
                    "tool_use_id": b.tool_use_id,
                    "content": b.content if isinstance(b.content, str) else str(b.content),
                    "is_error": b.is_error,
                }
            )
    return out
