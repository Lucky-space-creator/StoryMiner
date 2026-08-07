"""
章节漫剧路由（M15 章节漫剧列）

整体思路：
    暴露 M15 接口：章节漫剧列表/新建/详情/删除，均挂在 /novels/{id}/chapter-dramas 下，
    与小说资源同域。所有写接口依赖 get_current_user 取得 owner_id 实现数据隔离。

关键点：
    1. 路由前缀 /novels，与前端 API 契约一致。
    2. 响应统一 {code,msg,data}。

实现逻辑：
    委托 drama_service；归属校验在 service 内完成，路由仅做参数传递。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success
from common.exceptions import BizError
from services import drama_service
from repositories import novel_repo

router = APIRouter(prefix="/novels", tags=["chapter-dramas"])


class DramaIn(BaseModel):
    chapter_from: int
    chapter_to: int
    title: str | None = None
    summary: str | None = None


class SceneSaveIn(BaseModel):
    scene_design: list[str] = []
    plot_arrangement: list[str] = []
    camera_movement: list[str] = []
    duration_estimate: str | None = None
    content_raw: str | None = None


@router.get("/{novel_id}/chapter-dramas")
async def list_dramas(
    novel_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """章节漫剧列表（M15.1）。"""
    items = await drama_service.list_dramas(session, novel_id)
    return success(items)


@router.post("/{novel_id}/chapter-dramas")
async def create_drama(
    novel_id: int,
    body: DramaIn,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """新建章节漫剧（M15.2）：选定章节范围（≤5 章）并解析出场角色。"""
    novel = await novel_repo.get_novel(session, user.id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")
    data = await drama_service.create_drama(
        session, novel_id, user.id, body.model_dump())
    return success(data, "创建成功")


@router.get("/{novel_id}/chapter-dramas/{drama_id}")
async def get_drama(
    novel_id: int,
    drama_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """章节漫剧详情（M15.1），含已保存的导演场景分析。"""
    data = await drama_service.get_drama(session, drama_id, user.id)
    return success(data)


@router.post("/{novel_id}/chapter-dramas/{drama_id}/director-generate")
async def director_generate(
    novel_id: int,
    drama_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """导演 Agent 生成场景分析（M15.5）：返回四段式草稿，不落库。"""
    novel = await novel_repo.get_novel(session, user.id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")
    data = await drama_service.director_agent_generate(session, user.id, drama_id)
    return success(data, "生成完成")


@router.put("/{novel_id}/chapter-dramas/{drama_id}/scene")
async def save_scene(
    novel_id: int,
    drama_id: int,
    body: SceneSaveIn,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """保存导演场景分析（M15.5）：落库 story_chapter_drama_scene。"""
    novel = await novel_repo.get_novel(session, user.id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")
    data = await drama_service.save_drama_scene(
        session, user.id, drama_id, body.model_dump())
    return success(data, "已保存")


@router.delete("/{novel_id}/chapter-dramas/{drama_id}")
async def delete_drama(
    novel_id: int,
    drama_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """删除章节漫剧（逻辑删除）。"""
    await drama_service.delete_drama(session, drama_id, user.id)
    return success(None, "已删除")
