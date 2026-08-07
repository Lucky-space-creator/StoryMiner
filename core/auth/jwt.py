"""
JWT 鉴权

整体思路：
    签发/校验 Bearer Token，并提供 get_current_user 依赖做数据隔离。

关键点：
    1. create_access_token 将用户 id 写入 sub，设置过期。
    2. get_current_user 解析令牌并加载用户，失败抛 BizError(401)。
    3. 所有写操作依赖此依赖以获得 owner_id，实现多用户命名空间隔离。

实现逻辑：
    使用 python-jose 编解码；HTTPBearer(auto_error=False) 自行处理缺令牌场景。
"""
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_MINUTES
from db import get_session
from models.user import User
from sqlalchemy import select
from common.exceptions import BizError

_security = HTTPBearer(auto_error=False)


def create_access_token(user_id: int) -> str:
    """签发访问令牌，sub=用户ID，含过期时间。"""
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> int:
    """解码令牌返回用户ID，失败抛 BizError(401)。供 SSE 等无法携带请求头的场景使用。"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise BizError(401, "令牌无效或已过期")


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_security)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """依赖：解析 Bearer 令牌并返回当前用户，失败抛 BizError(401)。"""
    if not creds:
        raise BizError(401, "未登录或缺少令牌")
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        uid = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise BizError(401, "令牌无效或已过期")
    user = await session.get(User, uid)
    if not user:
        raise BizError(401, "用户不存在")
    return user


async def get_current_user_minimal(uid: int | None, session: Annotated[AsyncSession, Depends(get_session)]) -> User | None:
    """仅按用户 id 加载用户对象（不校验令牌），供 SSE 鉴权在已验 token 后取实体。"""
    if uid is None:
        return None
    return await session.get(User, uid)
