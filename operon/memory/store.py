"""记忆存储层: CRUD + BM25 搜索。


简化: 去掉 supersede chain (用 replace 直接更新), 去掉 categories, 去掉 user_id (单用户)。
保留: entity 分层 + evidence + origin + last_surfaced_at。
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from operon.db.schema import MemoryRecord

logger = logging.getLogger(__name__)


def _mem_id() -> str:
    return f"mem_{secrets.token_hex(6)}"


def _now() -> datetime:
    return datetime.now(UTC)


class MemoryStore:
    """记忆存储。通过 SQLAlchemy async session 操作 memories 表。"""

    def __init__(self, db_session_factory: Any = None) -> None:
        self.db_factory = db_session_factory

    async def list_by_entity(self, entity: str, frame_id: str | None = None) -> list[dict[str, Any]]:
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
        self, entity: str, body: str, *, evidence: str = "stated",
        origin: str = "user", frame_id: str | None = None,
    ) -> dict[str, Any] | None:
        """追加一条记忆。"""
        if self.db_factory is None:
            return None
        body = body.strip()[:1000]
        if not body:
            return None
        rec = MemoryRecord(
            id=_mem_id(),
            entity=entity,
            body=body,
            evidence=evidence,
            origin=origin,
            frame_id=frame_id if entity == "frame" else None,
            created_at=_now(),
            updated_at=_now(),
        )
        async with self.db_factory() as db:
            db.add(rec)
            await db.commit()
        return _row_to_dict(rec)

    async def replace(self, mem_id: str, body: str, *, evidence: str | None = None) -> bool:
        """更新一条记忆的内容。"""
        if self.db_factory is None:
            return False
        body = body.strip()[:1000]
        if not body:
            return False
        async with self.db_factory() as db:
            values: dict[str, Any] = {"body": body, "updated_at": _now()}
            if evidence:
                values["evidence"] = evidence
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
        "entity": r.entity,
        "body": r.body,
        "evidence": r.evidence,
        "origin": r.origin,
        "frame_id": r.frame_id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }
