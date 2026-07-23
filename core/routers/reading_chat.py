"""
阅读对话 REST 路由（M3）

整体思路：
    提供会话历史查询、设置更新、消息清空、附件上传等 HTTP 接口；流式对话走 WebSocket。

关键点：
    1. 鉴权：所有端点依赖 get_current_user，仅本人可见本人会话（用户/小说隔离）。
    2. 附件上传：复用 storage 包（本地/MinIO 透明切换），按 owner/novel 分目录隔离。
    3. 设置更新：llm_config_id / keep_recent / context_window / system_prompt 均落会话表。

实现逻辑：
    GET 取会话+未压缩消息；PUT 回写设置；DELETE 清空消息；POST 上传附件返回对象键。
"""
import os

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from auth.jwt import get_current_user
from common.response import success
from db import get_session
from storage import save as storage_save
from repositories.reading_chat_repo import (
    get_or_create_session,
    list_messages,
    update_settings,
    clear_messages,
)


router = APIRouter(prefix="/novels", tags=["reading-chat"])

_ALLOWED_EXT = {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg"}
_MAX_ATTACHMENT = 5 * 1024 * 1024


class SettingsIn(BaseModel):
    """阅读对话设置更新体。"""

    llm_config_id: int | None = None
    keep_recent: int | None = None
    context_window: int | None = None
    system_prompt: str | None = None


@router.get("/{novel_id}/reading-chat")
async def get_chat(
    novel_id: int, user=Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    """获取会话与历史消息（未压缩）。"""
    conv = await get_or_create_session(session, user.id, novel_id)
    msgs = await list_messages(session, conv.id)
    return success({
        "session": {
            "id": conv.id,
            "title": conv.title,
            "llm_config_id": conv.llm_config_id,
            "keep_recent": conv.keep_recent,
            "context_window": conv.context_window,
            "system_prompt": conv.system_prompt,
            "compressed_summary": conv.compressed_summary,
        },
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "attachments": m.attachments,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in msgs
        ],
    })


@router.put("/{novel_id}/reading-chat")
async def put_settings(
    novel_id: int,
    body: SettingsIn,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """更新会话设置（模型/保留条数/上下文窗口/系统提示）。"""
    conv = await get_or_create_session(session, user.id, novel_id)
    await update_settings(
        session,
        conv.id,
        llm_config_id=body.llm_config_id,
        keep_recent=body.keep_recent,
        context_window=body.context_window,
        system_prompt=body.system_prompt,
    )
    await session.commit()
    return success({"ok": True})


@router.delete("/{novel_id}/reading-chat")
async def clear_chat(
    novel_id: int, user=Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    """清空会话消息（保留会话记录）。"""
    conv = await get_or_create_session(session, user.id, novel_id)
    await clear_messages(session, conv.id)
    await session.commit()
    return success({"ok": True})


@router.post("/{novel_id}/reading-chat/attachments")
async def upload_attachment(
    novel_id: int,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """上传阅读对话附件，返回对象键（后续随消息一并提交）。"""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="不支持的附件类型")
    if len(data) > _MAX_ATTACHMENT:
        raise HTTPException(status_code=400, detail="附件过大（>5MB）")
    key, _ = await storage_save(
        user.id, novel_id, file.filename or f"attach{ext}", data
    )
    return success({"key": key, "name": file.filename, "size": len(data)})
