"""
数据库模块

整体思路：
    基于 SQLAlchemy 2.0 异步引擎创建 session 工厂，统一提供异步会话与 Base。

关键点：
    1. 使用 create_async_engine + async_sessionmaker。
    2. 导出 Base 供 ORM 模型继承。
    3. 提供 get_session 依赖供路由注入。

实现逻辑：
    引擎在模块加载时创建；get_session 为 FastAPI dependency，yield 会话并自动关闭。
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from config import DB_DSN

engine = create_async_engine(DB_DSN, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """依赖：提供异步数据库会话，请求结束自动关闭。"""
    async with SessionLocal() as session:
        yield session
