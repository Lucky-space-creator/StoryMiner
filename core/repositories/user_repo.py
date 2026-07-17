"""
用户数据访问

整体思路：
    封装 story_user 的查询与写入，强制按 username/owner 隔离。

关键点：
    1. get_by_username 用于登录与注册查重。
    2. create 由调用方负责提交。

实现逻辑：
    基于 SQLAlchemy async session 的 select/get。
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    """按用户名查询用户。"""
    res = await session.execute(select(User).where(User.username == username))
    return res.scalar_one_or_none()


async def get_by_id(session: AsyncSession, uid: int) -> User | None:
    """按 ID 查询用户。"""
    return await session.get(User, uid)


async def create(session: AsyncSession, user: User) -> User:
    """写入新用户（调用方提交）。"""
    session.add(user)
    await session.flush()
    return user
