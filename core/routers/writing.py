"""
续写与概览 REST 路由（M8 情节概览与续写）

整体思路：
    暴露概览/时间线/角色弧线（一次性生成）、续写版本列表、采纳为新章节等 REST 接口（前缀 /api/v1）；
    续写的流式生成走独立 WebSocket（见 routers/ws_write.py），本路由只负责非流式的元数据与版本管理。

关键点：
    1. 全部依赖 get_current_user 获得 owner_id，保证多用户命名空间隔离。
    2. 概览/时间线/角色弧线为实时 LLM 生成，不落库，直接返回文本。
    3. 采纳接口将已有续写版本写为新章节，复用 M1 的 Chapter 模型。

实现逻辑：
    路由函数薄封装，业务逻辑全在 writing_service；统一 success 响应契约。
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_session
from auth.jwt import get_current_user
from models.user import User
from services import writing_service
from common.response import success

router = APIRouter(prefix="/novels", tags=["M8续写概览"])


class AdoptReq(BaseModel):
    """采纳续写为新章节请求体。"""

    version_id: int


@router.post("/{novel_id}/summary")
async def summary(
    novel_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """生成情节概览（M8.1）。"""
    text = await writing_service.generate_summary(session, user.id, novel_id)
    return success({"text": text})


@router.post("/{novel_id}/timeline")
async def timeline(
    novel_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """生成时间线梳理（M8.2）。"""
    text = await writing_service.generate_timeline(session, user.id, novel_id)
    return success({"text": text})


@router.post("/{novel_id}/character-arc")
async def character_arc(
    novel_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """生成角色弧线概览（M8.3）。"""
    text = await writing_service.generate_character_arc(session, user.id, novel_id)
    return success({"text": text})


@router.get("/{novel_id}/continue-write/versions")
async def versions(
    novel_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """续写版本列表（M8.6）。"""
    return success(await writing_service.list_versions(session, user.id, novel_id))


@router.post("/{novel_id}/continue-write/adopt")
async def adopt(
    novel_id: int,
    req: AdoptReq,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """采纳续写为新章节（M8.7）。"""
    data = await writing_service.adopt_as_chapter(session, user.id, req.version_id)
    await session.commit()
    return success(data)


# ─────────────────────────────────────────────────────────────
# 用户编辑保存（M8.9）：前端修改续写内容后保存到 MinIO 新建 continue_write 目录
# ─────────────────────────────────────────────────────────────
class SaveContinueReq(BaseModel):
    """保存用户编辑后的续写内容请求体。"""

    content: str
    name: str | None = None


@router.post("/{novel_id}/continue-write/save")
async def save_continue(
    novel_id: int,
    req: SaveContinueReq,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """保存用户编辑后的续写到 MinIO（continue_write/{owner}/{novel}/ 目录）。"""
    data = await writing_service.save_continue_write(
        session, user.id, novel_id, req.content, req.name
    )
    await session.commit()
    return success(data, "已保存到 MinIO")


@router.get("/{novel_id}/continue-write/saved")
async def list_saved(
    novel_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """列出当前用户在该小说下保存的续写草稿。"""
    items = await writing_service.list_continue_writes(session, user.id, novel_id)
    return success({"items": items})


@router.get("/{novel_id}/continue-write/saved/content")
async def read_saved(
    novel_id: int,
    object_key: str = Query(..., description="续写文件对象键"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """回读某份续写草稿内容。"""
    content = await writing_service.read_continue_write(session, user.id, object_key)
    return success({"content": content, "object_key": object_key})
