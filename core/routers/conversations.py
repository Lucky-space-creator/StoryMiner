"""
对话 REST 路由（M7 用户 × 小说人物对话）

整体思路：
    暴露会话的建/列/详/删/收藏/导出 REST 接口（前缀 /api/v1），流式对话走独立 WebSocket
    （见 routers/ws_chat.py），本路由只负责会话元数据与历史管理。

关键点：
    1. 全部依赖 get_current_user 获得 owner_id，保证多用户命名空间隔离。
    2. 新建会话校验小说与人物归属，避免越权建档。
    3. 导出返回 markdown 文本与文件名，由前端触发下载。

实现逻辑：
    路由函数薄封装，业务逻辑全在 conversation_service；统一 success 响应契约。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_session
from auth.jwt import get_current_user
from models.user import User
from services import conversation_service
from common.response import success

router = APIRouter(prefix="/conversations", tags=["M7对话"])


class CreateConvReq(BaseModel):
    """新建会话请求体。"""

    novel_id: int
    character_ids: list[int]
    title: str | None = None


@router.post("")
async def create_conv(
    req: CreateConvReq,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """新建会话（M7.1）。"""
    data = await conversation_service.create_conversation(
        session, user.id, req.novel_id, req.character_ids, req.title
    )
    await session.commit()
    return success(data)


@router.get("")
async def list_conv(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """会话列表（M7.6）。"""
    return success(await conversation_service.list_conversations(session, user.id))


@router.get("/{conv_id}")
async def get_conv(
    conv_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """会话详情 + 多轮历史（M7.6）。"""
    return success(await conversation_service.get_conversation(session, user.id, conv_id))


@router.delete("/{conv_id}")
async def delete_conv(
    conv_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """删除会话（M7.6）。"""
    await conversation_service.delete_conversation(session, user.id, conv_id)
    await session.commit()
    return success(None)


@router.post("/{conv_id}/favorite")
async def favorite_conv(
    conv_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """收藏/取消收藏（M7.6）。"""
    await conversation_service.toggle_favorite(session, user.id, conv_id)
    await session.commit()
    return success(None)


@router.post("/{conv_id}/export")
async def export_conv(
    conv_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """导出对话为 Markdown（M7.8）。"""
    data = await conversation_service.export_markdown(session, user.id, conv_id)
    await session.commit()
    return success(data)
