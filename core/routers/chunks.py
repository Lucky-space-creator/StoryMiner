"""
切片路由（/api/v1/chunks，M4 文档切片展示）

整体思路：
    暴露 M4 全部接口：切片列表、详情、来源定位、关键词检索、相似切片、编辑内容、屏蔽/启用。

关键点：
    1. 全部依赖 get_current_user 取得 owner_id，实现多用户数据隔离。
    2. 编辑内容触发重向量化（复用 embed 适配器与 Chroma 向量库）。
    3. 路径顺序：/search、/{id}/similar、/{id}/source 必须在 /{id} 之前声明，
       避免被 /{id} 路径参数捕获。

实现逻辑：
    委托 chunk_service 编排；统一用 success/paginate 包装，对齐 {code,msg,data} 契约。
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success, paginate
from services import chunk_service

router = APIRouter(prefix="/chunks", tags=["chunks"])


class ChunkDisableReq(BaseModel):
    """屏蔽/启用入参（M4.7）。"""
    disabled: bool


class ChunkUpdateReq(BaseModel):
    """编辑切片内容入参（M4.6），触发重向量化。"""
    content: str


@router.get("/search")
async def search_chunks(
    q: str = Query(..., min_length=1, description="关键词"),
    page: int = 1, size: int = 20,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """关键词检索切片（M4.4）。"""
    return success(await chunk_service.search_chunks(session, user.id, q, page, size))


@router.get("")
async def list_chunks(
    kb_id: int | None = None,
    chapter_id: int | None = None,
    disabled: bool | None = None,
    page: int = 1, size: int = 20,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """切片列表（M4.1），支持知识库/章节/屏蔽状态过滤与分页。"""
    return success(await chunk_service.list_chunks(session, user.id, kb_id, chapter_id, disabled, page, size))


@router.get("/{chunk_id}/similar")
async def similar_chunks(
    chunk_id: int, top_k: int = 5,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """相似切片（M4.5），向量 top_k。"""
    return success(await chunk_service.similar_chunks(session, user.id, chunk_id, top_k))


@router.get("/{chunk_id}/source")
async def chunk_source(
    chunk_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """来源定位（M4.3）：章节 + 字符区间，供前端高亮。"""
    return success(await chunk_service.get_chunk_source(session, user.id, chunk_id))


@router.get("/{chunk_id}")
async def chunk_detail(
    chunk_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """切片详情（M4.2），含来源元信息。"""
    return success(await chunk_service.get_chunk_detail(session, user.id, chunk_id))


@router.put("/{chunk_id}")
async def update_chunk(
    chunk_id: int, req: ChunkUpdateReq,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """编辑切片内容（M4.6），触发重向量化。"""
    return success(await chunk_service.update_chunk_content(session, user.id, chunk_id, req.content))


@router.post("/{chunk_id}/disable")
async def disable_chunk(
    chunk_id: int, req: ChunkDisableReq,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """屏蔽/启用切片（M4.7）。"""
    return success(await chunk_service.set_disabled(session, user.id, chunk_id, req.disabled))
