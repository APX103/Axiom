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

from axiom_core.agent.runner import Agent, RunResult
from axiom_core.agent.session import Session, SessionConfig
from axiom_core.frames.service import FrameService
from axiom_core.llm.base import LLMClient
from axiom_core.llm.messages import Message, Role
from axiom_core.tools.context import PlanState, ToolContext

from .callbacks import WSCallbacks
from .events import plan_snapshot

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
    # Layer A.5: 所属 project id (从 SessionConfig 拷贝, DB 持久化 + 记忆隔离用)
    project_id: str | None = None
    # 建会话时的 provider 快照 (model, base_url, api_key)。
    # 热切换只在 "client 仍是创建时那个 且 settings 变了" 时进行;
    # 若 client 被外部替换过 (如测试注入 FakeLLM), 快照不匹配, 不动。
    provider_snapshot: tuple[str | None, str | None, str | None] | None = None

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
            from axiom_core.db.schema import SessionRecord

            async with self.db_session_factory() as db:
                rec = SessionRecord(
                    id=active.id,
                    title=None,
                    workspace=str(active.session.config.workspace),
                    model=active.session.config.model,
                    plan_mode=active.session.config.plan_mode,
                    status="active",
                    project_id=active.project_id,
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
            from axiom_core.db.schema import SessionMessage

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

            from axiom_core.db.schema import SessionRecord

            async with self.db_session_factory() as db:
                await db.execute(
                    update(SessionRecord)
                    .where(SessionRecord.id == sid, SessionRecord.title.is_(None))
                    .values(title=title[:100])
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to update title for session %s", sid)

    async def _db_save_plan(self, sid: str, plan: Any) -> None:
        """把 plan 快照持久化到 sessions 表。"""
        if not self.db_session_factory:
            return
        try:
            import json

            from sqlalchemy import update

            from axiom_core.db.schema import SessionRecord

            snapshot = plan_snapshot(plan)
            async with self.db_session_factory() as db:
                await db.execute(
                    update(SessionRecord)
                    .where(SessionRecord.id == sid)
                    .values(plan_data=json.dumps(snapshot, ensure_ascii=False))
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to persist plan for session %s", sid)

    async def _db_list_sessions(self) -> list[dict[str, Any]]:
        """从 DB 读取所有会话记录。"""
        if not self.db_session_factory:
            return []
        try:
            from sqlalchemy import select

            from axiom_core.db.schema import SessionRecord

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
                        # Layer A.5: project_id (老 session 迁移后是 proj_default)
                        "project_id": getattr(r, "project_id", None),
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

            from axiom_core.db.schema import SessionMessage

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

    async def _db_max_seq(self, sid: str) -> int:
        """返回某会话已存消息的最大 seq, 无消息返回 -1。"""
        if not self.db_session_factory:
            return -1
        try:
            from sqlalchemy import func, select

            from axiom_core.db.schema import SessionMessage

            async with self.db_session_factory() as db:
                result = await db.execute(
                    select(func.coalesce(func.max(SessionMessage.seq), -1)).where(
                        SessionMessage.session_id == sid
                    )
                )
                return result.scalar_one()
        except Exception:
            logger.exception("Failed to get max seq for session %s", sid)
            return -1

    async def _db_delete_session(self, sid: str) -> None:
        """从 DB 删除会话及其消息 (级联)。"""
        if not self.db_session_factory:
            return
        try:
            from sqlalchemy import delete

            from axiom_core.db.schema import SessionRecord

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

            from axiom_core.db.schema import SessionRecord, _now

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
        data_dir: Path | None = None,
        load_claude_skills: bool = True,
        load_project_skills: bool = True,
        skill_extra_dirs: list[str] | None = None,
        project_id: str | None = None,
    ) -> ActiveSession:
        """创建会话 (初始化 MCP + ctx,不启动 run) + 写 DB。

        Args:
            sid: 可选的 session id。传入时直接使用 (app 层需提前生成以计算 workspace 路径);
                 不传则在此处生成。
            project_id: Layer A.5 所属 project id。None 时 frame.project_id 也 None,
                        agent 第一次保存 artifact 时自动生成 proj_<root>。
        """
        import uuid

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
                data_dir=data_dir,
                load_claude_skills=load_claude_skills,
                load_project_skills=load_project_skills,
                skill_extra_dirs=skill_extra_dirs or [],
                project_id=project_id,
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
            project_id=project_id or ctx.project_id,
            provider_snapshot=(
                session.config.model,
                getattr(llm, "base_url", None),
                getattr(llm, "api_key", None),
            ),
        )
        self._sessions[sid] = active
        self._evict_if_needed()
        await self._db_save_session(active)
        return active

    async def get_or_restore(self, sid: str) -> ActiveSession:
        """获取活跃会话; 若已淘汰出内存, 则从 DB 重建运行时状态。"""
        active = self._sessions.get(sid)
        if active is not None:
            return active
        return await self.restore(sid)

    async def restore(self, sid: str) -> ActiveSession:
        """从 DB 恢复一个已归档/被淘汰的会话, 重建完整运行时状态。

        恢复内容包括:
        - workspace / model / plan_mode 等会话配置
        - 历史消息 (直接拼接到 ctx.frame.messages)
        - plan 状态 (PlanState)
        - 会话级 skill / MCP / api_keys 取当前 settings (与原会话一致)

        注意: 原会话若用请求体里临时传的 base_url/api_key 创建, 那些值未持久化,
        恢复时会使用当前 settings 中的对应配置。
        """
        if not self.db_session_factory:
            raise KeyError(f"session {sid} not found (no DB)")

        from sqlalchemy import select

        from axiom_core.config import load_settings
        from axiom_core.db.schema import SessionRecord
        from axiom_core.llm.openai_compat import OpenAICompatClient
        from axiom_core.mcp.manager import MCPServerConfig
        from axiom_core.settings import get_app_settings

        # 1. 读 DB 元数据
        async with self.db_session_factory() as db:
            result = await db.execute(select(SessionRecord).where(SessionRecord.id == sid))
            rec = result.scalar_one_or_none()
        if rec is None:
            raise KeyError(f"session {sid} not found")

        # 2. 用当前 settings 重建 LLM client; 优先匹配保存的 model 所在 tier
        settings = load_settings()
        saved_model = rec.model
        tier = None
        if settings.models:
            # 先按 model 名找对应 tier
            for name in ("large", "medium", "small", "kernel", "reviewer"):
                t = getattr(settings.models, name, None)
                if t and t.model == saved_model:
                    tier = t
                    break
            # 找不到就用默认 tier
            if tier is None:
                tier = settings.models.tier(settings.default_model_tier)

        if tier is None or not (tier.base_url and tier.api_key and tier.model):
            raise RuntimeError(
                f"无法恢复会话 {sid}: 当前 settings 缺少 LLM 配置 "
                f"(需要 base_url + api_key + model)"
            )

        llm = OpenAICompatClient(
            base_url=tier.base_url,
            api_key=tier.api_key,
            model=tier.model,
        )

        # 3. MCP / api_keys / skill 配置取当前 settings
        mcp_servers = None
        if settings.mcp_servers:
            mcp_servers = [
                MCPServerConfig(
                    name=s.get("name", f"mcp-{i}"),
                    url=s.get("url", ""),
                    headers=s.get("headers", {}),
                )
                for i, s in enumerate(settings.mcp_servers)
                if s.get("url")
            ] or None

        merged_keys = {k: v for k, v in settings.api_keys.items() if v} or None

        app_cfg = get_app_settings(settings.data_dir_resolved())

        workspace = Path(rec.workspace)

        callbacks = WSCallbacks()
        session = Session(
            llm=llm,
            config=SessionConfig(
                workspace=workspace,
                plan_mode=rec.plan_mode,
                max_iterations=40,
                model=tier.model,
                context_window=tier.context_window,
                mcp_servers=mcp_servers,
                api_keys=merged_keys or {},
                disabled_skills=app_cfg.disabled_skills or [],
                db_session_factory=self.db_session_factory,
                data_dir=settings.data_dir_resolved(),
                load_claude_skills=app_cfg.load_claude_skills,
                load_project_skills=app_cfg.load_project_skills,
                skill_extra_dirs=app_cfg.skill_extra_dirs or [],
            ),
            callbacks=callbacks,
        )
        ctx = await session.prepare()

        # 4. 把 DB 中的历史消息直接拼回 frame.messages
        db_messages = await self._db_load_messages(sid)
        for m in db_messages:
            msg = Message(
                role=Role(m["role"]),
                content=m["content"],
                _harness_notice=bool(m.get("harness_notice")),
            )
            ctx.frame.messages.append(msg)

        # 5. 恢复 plan 状态
        plan_snapshot = _parse_plan_data(rec.plan_data)
        if plan_snapshot:
            try:
                ctx.plan = PlanState(**plan_snapshot)
            except Exception:
                logger.exception("Failed to restore plan for session %s", sid)

        # 6. 回填元数据
        if rec.title:
            ctx.frame.task_summary = rec.title
        ctx.frame.created_at = rec.created_at
        ctx.frame.updated_at = rec.updated_at

        active = ActiveSession(
            id=sid,
            session=session,
            ctx=ctx,
            callbacks=callbacks,
            frame_service=session.frame_service,
            mcp_manager=session.mcp_manager,
            _msg_seq=await self._db_max_seq(sid) + 1,
            provider_snapshot=(tier.model, tier.base_url, tier.api_key),
        )
        self._sessions[sid] = active
        self._evict_if_needed()
        logger.info("Restored session %s from DB into memory", sid)
        return active

    def get(self, sid: str) -> ActiveSession | None:
        return self._sessions.get(sid)

    def _refresh_provider_if_changed(self, active: ActiveSession) -> bool:
        """若当前 settings 的启用 provider 与会话的 llm client 不一致, 重建 client。

        只在 "client 仍是建会话时那个 (provider_snapshot 匹配) 且 settings 变了" 时切换;
        client 被外部替换过 (如测试注入) 则不动。任何异常都不阻断 run (保留旧 client);
        返回是否发生了切换。
        """
        try:
            from axiom_core.config import load_settings
            from axiom_core.llm.openai_compat import OpenAICompatClient

            settings = load_settings()
            tier = settings.models.tier(settings.default_model_tier) if settings.models else None
            if tier is None or not (tier.base_url and tier.api_key and tier.model):
                return False
            cur = active.session.llm
            cur_sig = (
                active.session.config.model,
                getattr(cur, "base_url", None),
                getattr(cur, "api_key", None),
            )
            snapshot = getattr(active, "provider_snapshot", None)
            if snapshot is not None and cur_sig != snapshot:
                return False  # client 已被外部替换, 不归我们管
            new_sig = (tier.model, tier.base_url, tier.api_key)
            if new_sig == cur_sig:
                return False
            active.session.llm = OpenAICompatClient(
                base_url=tier.base_url, api_key=tier.api_key, model=tier.model
            )
            active.session.config.model = tier.model
            if tier.context_window:
                active.session.config.context_window = tier.context_window
            active.provider_snapshot = new_sig
            logger.info(
                "session %s: provider hot-swapped to %s (%s)",
                active.id,
                tier.model,
                tier.base_url,
            )
            return True
        except Exception:
            logger.exception("provider hot-swap check failed for session %s", active.id)
            return False

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

    async def run(
        self,
        sid: str,
        prompt: str,
        *,
        plan_mode: bool | None = None,
        deep_review: bool | None = None,
    ) -> RunResult:
        """启动 (或继续) 一个会话。事件经 callbacks.queue 流出。消息写 DB。

        Args:
            plan_mode: 覆盖会话级 plan_mode。None=用会话配置。
            deep_review: 覆盖会话级 deep_review (深度综述模式)。
        """
        active = self._sessions[sid]

        # 重入保护: 若上一轮 run 还在跑 (用户没收到 complete 就断连/重发),
        # 显式取消它, 否则两个 run 并发操作同一 frame/queue, 状态会乱。
        prev = active.running
        if prev is not None and not prev.done():
            prev.cancel()
            try:
                await prev
            except (asyncio.CancelledError, Exception):
                pass

        # 清空残留事件: queue 是 per-session 跨 run 共享的, 上轮 stop 后可能
        # 残留了 complete 等事件没被前端消费, 不清掉会让新 run 的第一个 queue.get()
        # 拿到旧的 complete → 流立刻结束, 前端以为"完成了"但新 run 根本没输出。
        while not active.callbacks.queue.empty():
            try:
                active.callbacks.queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Provider 热切换: 用户在设置里改了启用的 provider (或 key) 后,
        # 已驻内存的会话还拿着建会话时的旧 client, 之前必须重启 (走 restore) 才生效。
        # 这里在每次 run 前对比当前 settings 的启用 tier, 不一致就重建 client。
        self._refresh_provider_if_changed(active)

        msg_count_before = len(active.ctx.frame.messages)

        from axiom_core.tools.router import ToolRouter

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
            deep_review=bool(deep_review),
            callbacks=active.callbacks,
        )
        # 记录当前 task, 供重入保护 + LRU 判断 (start 前设置, finally 清掉)
        active.running = asyncio.current_task()
        try:
            result = await agent.run(prompt)
        finally:
            # 只在还是自己的 task 时清 (避免被更新的 run 覆盖)
            if active.running is asyncio.current_task():
                active.running = None
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

        await self._db_save_plan(sid, active.ctx.plan)
        await self._db_touch_session(sid)
        return result

    async def approve_plan(self, sid: str) -> dict[str, Any]:
        """批准 plan (从 awaiting_plan_approval 恢复)。"""
        from axiom_core.tools.builtins import plan as plan_tools

        active = await self.get_or_restore(sid)
        await plan_tools.approve_plan(active.ctx)
        await self._db_save_plan(sid, active.ctx.plan)
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
            "plan": plan_snapshot(active.ctx.plan),
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
            "plan": _parse_plan_data(rec.get("plan_data")),
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


def _parse_plan_data(raw: str | None) -> dict[str, Any] | None:
    """解析 DB 里的 plan_data JSON。"""
    if not raw:
        return None
    try:
        import json

        return json.loads(raw)
    except Exception:
        return None


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
