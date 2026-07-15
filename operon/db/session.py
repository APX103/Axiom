"""DB session 管理。

对应原版: 2533.js:397 N4_() 打开 DB + 2533.js:427 迁移。
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

    对应原版启动时的迁移 (2533.js:427)。
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
    return engine


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
