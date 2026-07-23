"""
数据库模块

整体思路：
    基于 SQLAlchemy 2.0 异步引擎创建 session 工厂，统一提供异步会话与 Base。
    接入 L1 数据库连接池治理：原本 create_async_engine 默认 pool_size=5，
    全流程大量并发分析与同步会话会耗尽连接；此处改为可从 config 调优的池参数。

关键点：
    1. 使用 create_async_engine + async_sessionmaker。
    2. 导出 Base 供 ORM 模型继承。
    3. 提供 get_session 依赖供路由注入。
    4. L1：pool_size / max_overflow / pool_timeout / pool_recycle / pool_pre_ping 由 config 注入，
       默认池上限 20（原 5），避免分析高峰期 "connection pool exhausted"；
       pool_pre_ping 开启后，每次取连接自动探活，规避 PostgreSQL 服务端 8h 断连（gone away）。

实现逻辑：
    引擎在模块加载时创建；get_session 为 FastAPI dependency，yield 会话并自动关闭。
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from config import (
    DB_DSN,
    DB_POOL_SIZE,
    DB_MAX_OVERFLOW,
    DB_POOL_TIMEOUT,
    DB_POOL_RECYCLE,
    DB_POOL_PRE_PING,
)

# L1 连接池治理：asyncpg 默认 pool_size=5、无 pre_ping、不回收空闲连接。
# 全流程分析（解析/切章/人物/章节/图谱）并发会话较多，5 个连接极易被打满；
# 上调至 20 + 探活 + 30min 回收，可稳定支撑并发链路。
engine = create_async_engine(
    DB_DSN,
    echo=False,
    future=True,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=DB_POOL_TIMEOUT,
    pool_recycle=DB_POOL_RECYCLE,
    pool_pre_ping=DB_POOL_PRE_PING,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """依赖：提供异步数据库会话，请求结束自动关闭。"""
    async with SessionLocal() as session:
        yield session
