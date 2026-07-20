"""DB session 管理。


原版用 bun:sqlite + worker_threads,本项目用 SQLAlchemy 2.0 (async) + aiosqlite。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .schema import Base


async def init_engine(db_url: str, *, echo: bool = False) -> AsyncEngine:
    """初始化引擎并建表。


    首版用 create_all + inline 迁移 (长期可能接 Alembic)。

    db_url 若为裸 sqlite:/// 会被规范化为 sqlite+aiosqlite:/// (async driver)。
    """
    if db_url.startswith("sqlite:///") and "+" not in db_url.split("sqlite", 2)[1]:
        db_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    # sqlite 需开启外键约束 (原版默认开)
    engine = create_async_engine(db_url, echo=echo)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA foreign_keys = ON")
        await conn.exec_driver_sql("PRAGMA journal_mode = WAL")
        await conn.exec_driver_sql("PRAGMA busy_timeout = 5000")
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_add_plan_data(conn)
        await _migrate_memory_layer_a(conn)
    return engine


async def _migrate_add_plan_data(conn) -> None:
    """兼容性迁移: 为旧版 sessions 表增加 plan_data 列。"""
    try:
        from sqlalchemy import inspect

        def _check_and_add(sync_conn):
            inspector = inspect(sync_conn)
            cols = {c["name"] for c in inspector.get_columns("sessions")}
            if "plan_data" not in cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE sessions ADD COLUMN plan_data TEXT"
                )

        await conn.run_sync(_check_and_add)
    except Exception:
        # 迁移失败不阻断启动; 下次启动再尝试
        pass


async def _migrate_memory_layer_a(conn) -> None:
    """Layer A 迁移: memories 表加 5 列 (scope/entity_type/session_id/confidence/meta)。

    老数据处理:
    - scope 从老 entity 列拷贝
    - entity_type 默认 'note' (未分类的旧记忆)
    - origin 'user' → 'user_stated', 'extractor' → 'extractor' (后者不变)
    - confidence 默认 0.5

    迁移失败不阻断启动; 下次启动再尝试。
    """
    try:
        from sqlalchemy import inspect

        def _check_and_migrate(sync_conn):
            inspector = inspect(sync_conn)
            cols = {c["name"] for c in inspector.get_columns("memories")}

            # 1. 加新列 (SQLite ALTER TABLE 不能 IF NOT EXISTS, 先检查)
            if "scope" not in cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE memories ADD COLUMN scope VARCHAR(20)"
                )
            if "entity_type" not in cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE memories ADD COLUMN entity_type VARCHAR(20) DEFAULT 'note' NOT NULL"
                )
            if "session_id" not in cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE memories ADD COLUMN session_id VARCHAR(50)"
                )
            if "confidence" not in cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE memories ADD COLUMN confidence FLOAT DEFAULT 0.5 NOT NULL"
                )
            if "meta" not in cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE memories ADD COLUMN meta TEXT"
                )

            # 2. 数据迁移: scope 从 entity 拷贝 (只更新 NULL 的, 避免覆盖已迁移的)
            sync_conn.exec_driver_sql(
                "UPDATE memories SET scope = entity WHERE scope IS NULL"
            )

            # 3. origin 多值化 (老 'user' → 'user_stated')
            sync_conn.exec_driver_sql(
                "UPDATE memories SET origin = 'user_stated' WHERE origin = 'user'"
            )

            # 4. 加索引 (CREATE INDEX IF NOT EXISTS 是 SQLite 原生支持的)
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_memories_entity_type ON memories (entity_type)"
            )
            sync_conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_memories_session_id ON memories (session_id)"
            )

        await conn.run_sync(_check_and_migrate)
    except Exception:
        # 迁移失败不阻断启动; 下次启动再尝试
        pass


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
