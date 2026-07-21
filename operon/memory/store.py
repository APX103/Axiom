"""记忆存储层: CRUD + BM25 搜索。


Layer A 升级 (2026-07):
- append() 支持新字段: scope / entity_type / meta / session_id / confidence
- 新增 list_by_type() / list_by_session() 查询接口
- _row_to_dict() 返回所有新字段
- 向后兼容: 不传新字段时, scope=entity, entity_type='note', meta=None

简化: 去掉 supersede chain (用 replace 直接更新), 去掉 user_id (单用户)。
保留: entity 分层 + evidence + origin + last_surfaced_at。
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update

from operon.db.schema import MemoryRecord

logger = logging.getLogger(__name__)


def _mem_id() -> str:
    return f"mem_{secrets.token_hex(6)}"


def _now() -> datetime:
    return datetime.now(UTC)


def _meta_to_str(meta: dict[str, Any] | None) -> str | None:
    """dict → JSON 字符串 (None 透传)。"""
    if meta is None:
        return None
    return json.dumps(meta, ensure_ascii=False)


def _meta_from_str(s: str | None) -> dict[str, Any] | None:
    """JSON 字符串 → dict (None/空 透传, 解析失败返回 None)。"""
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


class MemoryStore:
    """记忆存储。通过 SQLAlchemy async session 操作 memories 表。"""

    def __init__(self, db_session_factory: Any = None) -> None:
        self.db_factory = db_session_factory

    async def list_by_entity(
        self, entity: str, frame_id: str | None = None
    ) -> list[dict[str, Any]]:
        """列出某层的所有记忆。"""
        if self.db_factory is None:
            return []
        async with self.db_factory() as db:
            stmt = select(MemoryRecord).where(MemoryRecord.entity == entity)
            if entity == "frame" and frame_id:
                stmt = stmt.where(MemoryRecord.frame_id == frame_id)
            stmt = stmt.order_by(MemoryRecord.created_at.desc())
            result = await db.execute(stmt)
            rows = result.scalars().all()
            return [_row_to_dict(r) for r in rows]

    async def list_by_type(self, entity_type: str) -> list[dict[str, Any]]:
        """列出某语义类型的所有记忆 (Layer A): claim/evidence/citation/tool_use/note。"""
        if self.db_factory is None:
            return []
        async with self.db_factory() as db:
            result = await db.execute(
                select(MemoryRecord)
                .where(MemoryRecord.entity_type == entity_type)
                .order_by(MemoryRecord.created_at.desc())
            )
            rows = result.scalars().all()
            return [_row_to_dict(r) for r in rows]

    async def list_by_session(self, session_id: str) -> list[dict[str, Any]]:
        """列出某 session 的所有记忆 (Layer A: 跨会话溯源)。"""
        if self.db_factory is None:
            return []
        async with self.db_factory() as db:
            result = await db.execute(
                select(MemoryRecord)
                .where(MemoryRecord.session_id == session_id)
                .order_by(MemoryRecord.created_at.desc())
            )
            rows = result.scalars().all()
            return [_row_to_dict(r) for r in rows]

    async def list_by_project(self, project_id: str) -> list[dict[str, Any]]:
        """列出某 project 的所有记忆 (Layer A.5: project 隔离)。

        注意: profile 层 project_id=NULL, 不会被包含。要拿 profile 层记忆,
        用 list_by_entity('profile') 或 list_all 后过滤。
        """
        if self.db_factory is None:
            return []
        async with self.db_factory() as db:
            result = await db.execute(
                select(MemoryRecord)
                .where(MemoryRecord.project_id == project_id)
                .order_by(MemoryRecord.created_at.desc())
            )
            rows = result.scalars().all()
            return [_row_to_dict(r) for r in rows]

    async def list_all(self) -> list[dict[str, Any]]:
        """列出所有记忆 (搜索用)。"""
        if self.db_factory is None:
            return []
        async with self.db_factory() as db:
            result = await db.execute(
                select(MemoryRecord).order_by(MemoryRecord.created_at.desc())
            )
            rows = result.scalars().all()
            return [_row_to_dict(r) for r in rows]

    async def append(
        self,
        entity: str,
        body: str,
        *,
        evidence: str = "stated",
        origin: str = "user_stated",
        frame_id: str | None = None,
        # Layer A 新字段
        scope: str | None = None,
        entity_type: str = "note",
        meta: dict[str, Any] | None = None,
        session_id: str | None = None,
        confidence: float = 0.5,
        # Layer A.5: 所属 project
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        """追加一条记忆。

        向后兼容: 老调用只传 entity+body+evidence+origin+frame_id 仍可用。
        新调用可传 scope/entity_type/meta/session_id/confidence/project_id。

        - scope: 不传时默认等于 entity (向后兼容)
        - entity_type: 默认 'note' (未分类)
        - meta: dict 会被序列化为 JSON 字符串存库
        - project_id: Layer A.5; profile 层应传 None (跨 project 共享),
          其他层传当前 project id 实现隔离。
        """
        if self.db_factory is None:
            return None
        body = body.strip()[:1000]
        if not body:
            return None
        # Layer A.5: profile 层 project_id 强制 None (跨 project 共享用户偏好)
        eff_scope = scope if scope is not None else entity
        eff_project_id = None if eff_scope == "profile" else project_id
        rec = MemoryRecord(
            id=_mem_id(),
            entity=entity,
            scope=eff_scope,
            entity_type=entity_type,
            body=body,
            evidence=evidence,
            origin=origin,
            frame_id=frame_id if entity == "frame" else None,
            session_id=session_id,
            project_id=eff_project_id,
            confidence=confidence,
            meta=_meta_to_str(meta),
            created_at=_now(),
            updated_at=_now(),
        )
        async with self.db_factory() as db:
            db.add(rec)
            await db.commit()
        return _row_to_dict(rec)

    async def replace(
        self,
        mem_id: str,
        body: str,
        *,
        evidence: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> bool:
        """更新一条记忆的内容。

        Layer A: meta 参数可选, 传入则更新 (传 None 不动 meta)。
        """
        if self.db_factory is None:
            return False
        body = body.strip()[:1000]
        if not body:
            return False
        async with self.db_factory() as db:
            values: dict[str, Any] = {"body": body, "updated_at": _now()}
            if evidence:
                values["evidence"] = evidence
            if meta is not None:
                values["meta"] = _meta_to_str(meta)
            result = await db.execute(
                update(MemoryRecord).where(MemoryRecord.id == mem_id).values(**values)
            )
            await db.commit()
            return result.rowcount > 0

    async def remove(self, mem_id: str) -> bool:
        """删除一条记忆。"""
        if self.db_factory is None:
            return False
        async with self.db_factory() as db:
            result = await db.execute(
                delete(MemoryRecord).where(MemoryRecord.id == mem_id)
            )
            await db.commit()
            return result.rowcount > 0

    async def clear_frame(self, frame_id: str) -> int:
        """清除某会话的所有 frame 记忆 (删会话时调用)。"""
        if self.db_factory is None:
            return 0
        async with self.db_factory() as db:
            result = await db.execute(
                delete(MemoryRecord).where(
                    MemoryRecord.entity == "frame",
                    MemoryRecord.frame_id == frame_id,
                )
            )
            await db.commit()
            return result.rowcount

    async def mark_surfaced(self, mem_ids: list[str]) -> None:
        """更新最后召回时间。"""
        if not mem_ids or self.db_factory is None:
            return
        async with self.db_factory() as db:
            await db.execute(
                update(MemoryRecord)
                .where(MemoryRecord.id.in_(mem_ids))
                .values(last_surfaced_at=_now())
            )
            await db.commit()


def _row_to_dict(r: MemoryRecord) -> dict[str, Any]:
    return {
        "id": r.id,
        # 老字段
        "entity": r.entity,
        "body": r.body,
        "evidence": r.evidence,
        "origin": r.origin,
        "frame_id": r.frame_id,
        # Layer A 新字段
        "scope": r.scope if r.scope is not None else r.entity,
        "entity_type": r.entity_type,
        "meta": _meta_from_str(r.meta),
        "session_id": r.session_id,
        "project_id": r.project_id,
        "confidence": r.confidence,
        # 时间戳
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "last_surfaced_at": r.last_surfaced_at.isoformat() if r.last_surfaced_at else None,
    }
