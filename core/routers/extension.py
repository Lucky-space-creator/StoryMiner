"""
扩展功能路由（M13 扩展功能）。

整体思路：
    暴露前端 ExploreView 所需接口：全局搜索(/search)、笔记(/notes)、标签(/tags)、
    收藏(/favorites)、审计日志(/audit-logs)，并补阅读进度(/reading-progress)。

关键点：
    1. 依赖 get_current_user 取 owner_id，实现数据隔离。
    2. 路径与前端 explore.js / dashboard 契约对齐，响应统一 {code,msg,data}。
    3. 写接口用内联 Pydantic 模型做入参校验，保持最小实现。

实现逻辑：
    委托 extension_service 完成业务编排。
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success
from services import extension_service

router = APIRouter(tags=["extension"])


# ---------------- 入参模型（均含必填与长度校验，防止脏数据） ----------------
class NoteIn(BaseModel):
    """笔记入参（M13.2）。title/content/target_type 必填。"""
    title: str = Field(..., min_length=1, max_length=200, description="笔记标题必填")
    content: str = Field(..., min_length=1, description="笔记内容必填")
    target_type: str = Field(..., min_length=1, max_length=32, description="关联类型必填(novel/chapter/chunk)")
    target_id: int | None = Field(None, description="关联对象 id")


class TagIn(BaseModel):
    """标签入参（M13.4）。name 必填。"""
    name: str = Field(..., min_length=1, max_length=32, description="标签名必填")
    color: str = Field("", max_length=16, description="颜色(可选)")


class FavoriteIn(BaseModel):
    """收藏入参（M13.6）。title/target_type/target_id 必填。"""
    title: str = Field(..., min_length=1, max_length=200, description="收藏标题必填")
    target_type: str = Field(..., min_length=1, max_length=32, description="收藏类型必填")
    target_id: int = Field(..., gt=0, description="收藏对象 id 必填且大于0")


class ProgressIn(BaseModel):
    """阅读进度入参（M13.3）。position 必填且非负。"""
    chapter_id: int | None = Field(None, description="章节 id(可选)")
    position: int = Field(0, ge=0, description="阅读进度位置(非负)")


# ---------------- 全局搜索（M13.1） ----------------
@router.get("/search")
async def global_search(
    q: str = Query("", description="搜索关键词"),
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """跨小说/章节/切片/人物统一检索。"""
    if not q.strip():
        return success([])
    return success(await extension_service.global_search(session, user.id, q.strip()))


# ---------------- 笔记（M13.2） ----------------
@router.get("/notes")
async def list_notes(user: User = Depends(get_current_user), session=Depends(get_session)):
    """笔记列表。"""
    return success(await extension_service.list_notes(session, user.id))


@router.post("/notes")
async def create_note(data: NoteIn, user: User = Depends(get_current_user), session=Depends(get_session)):
    """新建笔记。"""
    return success(await extension_service.create_note(session, user.id, data.model_dump()), "已保存")


@router.delete("/notes/{note_id}")
async def delete_note(note_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """删除笔记。"""
    await extension_service.delete_note(session, user.id, note_id)
    return success(msg="已删除")


# ---------------- 标签（M13.4） ----------------
@router.get("/tags")
async def list_tags(user: User = Depends(get_current_user), session=Depends(get_session)):
    """标签列表（字符串数组）。"""
    return success(await extension_service.list_tags(session, user.id))


@router.post("/tags")
async def create_tag(data: TagIn, user: User = Depends(get_current_user), session=Depends(get_session)):
    """新建标签。"""
    return success(await extension_service.create_tag(session, user.id, data.name, data.color), "已创建")


# ---------------- 收藏（M13.6） ----------------
@router.get("/favorites")
async def list_favorites(user: User = Depends(get_current_user), session=Depends(get_session)):
    """收藏列表。"""
    return success(await extension_service.list_favorites(session, user.id))


@router.post("/favorites")
async def create_favorite(data: FavoriteIn, user: User = Depends(get_current_user), session=Depends(get_session)):
    """新建收藏。"""
    return success(await extension_service.create_favorite(session, user.id, data.model_dump()), "已收藏")


@router.delete("/favorites/{fav_id}")
async def delete_favorite(fav_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """取消收藏。"""
    await extension_service.delete_favorite(session, user.id, fav_id)
    return success(msg="已取消收藏")


# ---------------- 审计日志（M13.7） ----------------
@router.get("/audit-logs")
async def list_audit_logs(user: User = Depends(get_current_user), session=Depends(get_session)):
    """审计日志列表。"""
    return success(await extension_service.list_audit_logs(session, user.id))


# ---------------- 阅读进度（M13.3） ----------------
@router.get("/reading-progress/{novel_id}")
async def get_progress(novel_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """取某本小说阅读进度。"""
    return success(await extension_service.get_progress(session, user.id, novel_id))


@router.put("/reading-progress/{novel_id}")
async def save_progress(
    novel_id: int, data: ProgressIn,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """保存阅读进度。"""
    return success(await extension_service.save_progress(session, user.id, novel_id, data.chapter_id, data.position), "已保存")
