"""
用户业务逻辑

整体思路：
    注册/登录/当前用户，密码哈希与令牌签发在此聚合。

关键点：
    1. 注册校验用户名唯一。
    2. authenticate 校验密码返回用户或 None。
    3. to_out 屏蔽敏感字段；login_token 签发令牌。

实现逻辑：
    调用 user_repo 与 auth 模块，事务在 service 内提交。
"""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from schemas.user import UserCreate
from models.user import User
from repositories import user_repo
from auth.security import hash_password, verify_password
from auth.jwt import create_access_token
from common.exceptions import BizError


def _iso(dt) -> str | None:
    """时间转 ISO 字符串。"""
    return dt.isoformat() if isinstance(dt, datetime) else None


def to_out(user: User) -> dict:
    """用户出参（不含密码）。"""
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "avatar": user.avatar,
        "created_at": _iso(user.created_at),
    }


def login_token(user: User) -> dict:
    """签发登录令牌。"""
    return {"access_token": create_access_token(user.id), "token_type": "bearer"}


async def register(session: AsyncSession, data: UserCreate) -> User:
    """注册：校验唯一性并哈希密码。"""
    if await user_repo.get_by_username(session, data.username):
        raise BizError(409, "用户名已存在")
    user = User(
        username=data.username,
        password=hash_password(data.password),
        name=data.name,
    )
    await user_repo.create(session, user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate(session: AsyncSession, username: str, password: str) -> User | None:
    """登录：校验用户名与密码，成功返回用户否则 None。"""
    user = await user_repo.get_by_username(session, username)
    if not user or not verify_password(password, user.password):
        return None
    return user
