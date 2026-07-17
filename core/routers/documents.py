"""
文档路由（/api/v1/documents）

整体思路：
    暴露文档级操作（M2.8 删除文档），独立于 /knowledge-bases 以对齐 API 路径 `/documents/{id}`。

关键点：
    1. 删除按 owner 隔离，逻辑删除文档并硬删其切片。
    2. 响应统一 {code,msg,data}。

实现逻辑：
    委托 kb_service.delete_document。
"""
from fastapi import APIRouter, Depends

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success
from services import kb_service

router = APIRouter(prefix="/documents", tags=["documents"])


@router.delete("/{doc_id}")
async def delete_document(doc_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """删除文档（M2.8）。"""
    await kb_service.delete_document(session, user.id, doc_id)
    return success(msg="已删除")
