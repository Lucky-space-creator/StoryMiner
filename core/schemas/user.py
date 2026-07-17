"""
用户请求/响应模型

整体思路：
    定义注册/登录入参与用户/令牌出参，屏蔽敏感字段。

关键点：
    1. 注册密码最短 6 位，由 pydantic 校验。
    2. UserOut 不含 password。

实现逻辑：
    继承 pydantic BaseModel。
"""
from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    """注册入参。"""
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    name: str | None = None


class UserLogin(BaseModel):
    """登录入参。"""
    username: str
    password: str


class UserOut(BaseModel):
    """用户出参（不含密码）。"""
    id: int
    username: str
    name: str | None = None
    avatar: str | None = None
    created_at: str | None = None


class TokenOut(BaseModel):
    """登录令牌出参。"""
    access_token: str
    token_type: str = "bearer"
