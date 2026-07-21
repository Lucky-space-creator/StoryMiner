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
from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success
from common.exceptions import BizError
from common import task_queue
from services import character_service, task_service
from repositories import character_repo, novel_repo

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
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """AI 生成小传（M6.2）：后台异步生成结构化档案并写回，立即返回统一 task_id。"""
    char = await character_repo.get(session, char_id)
    if not char or char.owner_id != user.id or char.deleted_at is not None:
        raise BizError(404, "人物不存在")
    novel = await novel_repo.get_novel(session, user.id, char.novel_id)
    novel_name = novel.name if novel else f"人物{char.name}"
    task = await task_service.create_task(session, user.id, "character", f"小说{novel_name}-人物抽取实体", novel_id=char.novel_id, target_id=char_id)
    task_queue.submit(character_service.generate_profile_async, char_id, user.id, task.id)
    return success({"task_id": task.id}, "已启动小传生成")


@novel_router.post("/{novel_id}/analyze-characters")
async def analyze_characters(
    novel_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """任务分析（小说详情页）：基于小说简介和正文，LLM 自动分析并创建人物档案。

    后台异步执行，立即返回统一 task_id 供仪表盘轮询进度。
    """
    novel = await novel_repo.get_novel(session, user.id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")
    task = await task_service.create_task(
        session, user.id, "character_analysis",
        f"小说{novel.name}-人物分析", novel_id=novel_id,
    )
    task_queue.submit(
        character_service.analyze_and_create_characters,
        novel_id=novel_id, owner_id=user.id,
        novel_name=novel.name, summary=novel.summary or "",
        async_task_id=task.id,
    )
    return success({"task_id": task.id}, "已启动人物分析")
