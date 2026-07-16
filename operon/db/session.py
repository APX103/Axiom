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


    首版用 create_all (后续接 Alembic 迁移)。

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


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
