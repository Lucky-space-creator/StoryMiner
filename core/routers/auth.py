"""
认证路由（/api/v1/auth）

整体思路：
    暴露注册/登录/当前用户接口，响应统一 {code,msg,data}。

关键点：
    1. /auth/* 无需登录；其余接口经 get_current_user 鉴权。
    2. 登录失败返回 BizError(401)，由全局处理器转 {code:401}。

实现逻辑：
    直接调用 user_service 与 auth.jwt。
"""
from fastapi import APIRouter, Depends

from schemas.user import UserCreate, UserLogin
from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success
from services import user_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register")
async def register(data: UserCreate, session=Depends(get_session)):
    """注册新用户并直接签发令牌（与登录保持一致）。"""
    user = await user_service.register(session, data)
    return success(user_service.login_token(user) | {"user": user_service.to_out(user)}, "注册成功")


@router.post("/login")
async def login(data: UserLogin, session=Depends(get_session)):
    """登录并返回访问令牌。"""
    user = await user_service.authenticate(session, data.username, data.password)
    if not user:
        from common.exceptions import BizError
        raise BizError(401, "用户名或密码错误")
    return success(user_service.login_token(user))


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    """获取当前用户信息。"""
    return success(user_service.to_out(user))
