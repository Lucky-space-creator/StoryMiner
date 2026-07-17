"""
人物档案路由（M6 小说人物信息简介）

整体思路：
    暴露 M6 接口：人物列表/新建（与小说同域，挂 /novels）、人物详情/编辑/删除/AI 生成
    （挂 /characters）。所有写接口依赖 get_current_user 取得 owner_id 实现数据隔离。

关键点：
    1. 列表与新建挂在 /novels/{id} 下（与前端 API 契约一致）；其余挂在 /characters/{id}。
    2. 响应统一 {code,msg,data}；AI 生成为同步阻塞（耗时操作，前端 loading 等待）。

实现逻辑：
    委托 character_service；归属校验在 service 内完成，路由仅做参数传递。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success
from common.exceptions import BizError
from services import character_service

# 列表/新建：与小说资源同域
novel_router = APIRouter(prefix="/novels", tags=["characters"])
# 详情/编辑/删除/生成：独立人物端点
char_router = APIRouter(prefix="/characters", tags=["characters"])


class CharacterIn(BaseModel):
    name: str
    role: str = "配角"
    desc: str | None = None


class CharacterUpdate(BaseModel):
    role: str | None = None
    gender: str | None = None
    identity: str | None = None
    personality: str | None = None
    appearance: str | None = None
    catchphrase: str | None = None
    desc: str | None = None
    avatar: str | None = None


@novel_router.get("/{novel_id}/characters")
async def list_characters(
    novel_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """人物列表（M6.1）。"""
    items = await character_service.list_characters(session, novel_id)
    return success(items)


@novel_router.post("/{novel_id}/characters")
async def create_character(
    novel_id: int,
    body: CharacterIn,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """新建人物（M6.1/M6.7）。"""
    data = await character_service.create_character(session, novel_id, user.id, body.model_dump())
    return success(data, "创建成功")


@char_router.get("/{char_id}")
async def get_character(
    char_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """人物详情（M6.1）。"""
    data = await character_service.get_character(session, char_id, user.id)
    return success(data)


@char_router.put("/{char_id}")
async def update_character(
    char_id: int,
    body: CharacterUpdate,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """编辑人物档案（M6.7）。"""
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    data = await character_service.update_character(session, char_id, user.id, payload)
    return success(data, "更新成功")


@char_router.delete("/{char_id}")
async def delete_character(
    char_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """删除人物（软删除）。"""
    await character_service.delete_character(session, char_id, user.id)
    return success(None, "已删除")


@char_router.post("/{char_id}/generate")
async def generate_profile(
    char_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """AI 生成小传（M6.2）：基于全文生成结构化档案并写回。"""
    data = await character_service.generate_profile(session, char_id, user.id)
    return success(data, "小传已生成")
