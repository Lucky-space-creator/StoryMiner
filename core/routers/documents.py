"""
文档路由（/api/v1/documents）

整体思路：
    暴露文档级操作（M2.8 删除文档、重命名），独立于 /knowledge-bases 以对齐 API 路径 `/documents/{id}`。
    方案A 下文档归属小说，删除按 owner 隔离，逻辑删除文档并清其切片与向量；重命名仅改名称。

关键点：
    1. 删除/重命名均按 owner 隔离，逻辑删除文档并硬删其切片。
    2. 响应统一 {code,msg,data}。

实现逻辑：
    委托 kb_service.delete_document；重命名直接改文档名后提交。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success
from common.exceptions import BizError
from services import kb_service
from repositories import novel_repo

router = APIRouter(prefix="/documents", tags=["documents"])


class DocRenameReq(BaseModel):
    """文档重命名入参。"""
    name: str = Field(..., min_length=1, max_length=255)


@router.delete("/{doc_id}")
async def delete_document(doc_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """删除文档（M2.8）。"""
    await kb_service.delete_document(session, user.id, doc_id)
    return success(msg="已删除")


@router.put("/{doc_id}")
async def rename_document(
    doc_id: int, req: DocRenameReq,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """重命名文档（CRUD 之 U）。"""
    doc = await novel_repo.get_document(session, doc_id)
    if not doc or doc.owner_id != user.id or doc.deleted_at is not None:
        raise BizError(404, "文档不存在")
    doc.name = req.name
    await session.commit()
    return success(msg="已重命名")
