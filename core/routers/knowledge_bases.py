"""
知识库路由（/api/v1/knowledge-bases）

整体思路：
    暴露 M2 全部接口：知识库 CRUD、文档追加/列表、统计、导出。

关键点：
    1. 所有接口依赖 get_current_user 取得 owner_id，实现数据隔离。
    2. 追加文档为 multipart 文件上传，复用 M1 解析链路并绑定目标库。
    3. 响应统一 {code,msg,data}；列表分页返回 {list,total,page,size}；导出按 format 返回 JSON/CSV。

实现逻辑：
    委托 kb_service；导出用 Response/JSONResponse 直接下发文件内容。
"""
import uuid

from fastapi import APIRouter, Depends, UploadFile, BackgroundTasks, Query
from fastapi.responses import Response, JSONResponse

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success, paginate
from schemas.knowledge_base import KBCreate, KBUpdate
from schemas.chunk import ChunkRequest
from services import kb_service, chunk_service

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


@router.post("")
async def create_kb(
    data: KBCreate,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """建库（M2.1）。"""
    return success(await kb_service.create_kb(session, user.id, data), "创建成功")


@router.get("")
async def list_kbs(
    novel_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """知识库列表（M2.2，可按 novel_id 过滤）。"""
    items, total = await kb_service.list_kbs(session, user.id, novel_id, page, size)
    return success(paginate(items, total, page, size))


@router.get("/{kb_id}")
async def get_kb(kb_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """知识库详情（M2.3）。"""
    return success(await kb_service.get_kb(session, user.id, kb_id))


@router.put("/{kb_id}")
async def update_kb(
    kb_id: int, data: KBUpdate,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """更新知识库（M2.4）。"""
    return success(await kb_service.update_kb(session, user.id, kb_id, data))


@router.delete("/{kb_id}")
async def delete_kb(kb_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """删除知识库（M2.5，级联切片/文档）。"""
    await kb_service.delete_kb(session, user.id, kb_id)
    return success(msg="已删除")


@router.post("/{kb_id}/documents")
async def append_document(
    kb_id: int, file: UploadFile,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """追加文档（M2.6）：上传文件到指定库并触发解析。"""
    result = await kb_service.append_document(session, user.id, kb_id, file, background_tasks)
    return success(result, "上传成功，开始解析")


@router.get("/{kb_id}/documents")
async def list_documents(
    kb_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """知识库文档列表（M2.7）。"""
    items, total = await kb_service.list_documents(session, user.id, kb_id, page, size)
    return success(paginate(items, total, page, size))


@router.get("/{kb_id}/stats")
async def kb_stats(kb_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """知识库统计（M2.9）。"""
    return success(await kb_service.stats(session, user.id, kb_id))


@router.get("/{kb_id}/export")
async def export_kb(
    kb_id: int,
    format: str = Query("json", pattern="^(json|csv)$"),
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """导出配置+切片（M2.10，JSON/CSV）。"""
    content, media_type, filename = await kb_service.export(session, user.id, kb_id, format)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if format == "csv":
        return Response(content=content, media_type=media_type, headers=headers)
    return JSONResponse(content=content, headers=headers)


@router.post("/{kb_id}/chunk")
async def chunk_kb(
    kb_id: int, data: ChunkRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """触发切割+向量化（M3.1）：后台任务执行，立即返回 task_id 供前端轮询进度。"""
    task_id = str(uuid.uuid4())
    background_tasks.add_task(chunk_service.chunk_and_index_async, user.id, kb_id, data, False, task_id)
    return success({"task_id": task_id}, "已启动切割")


@router.get("/{kb_id}/chunk-progress")
async def chunk_progress(
    kb_id: int, task_id: str = Query(...),
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """切割进度查询（M3.1 配套）：按 task_id 返回进度/阶段/状态。"""
    prog = chunk_service.get_chunk_progress(task_id)
    if not prog:
        raise BizError(404, "任务不存在或已过期")
    return success(prog)


@router.put("/{kb_id}/chunk-strategy")
async def update_chunk_strategy(
    kb_id: int, data: ChunkRequest,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """更新切片策略（M3.2）：存于 kb.extra.chunk_strategy。"""
    return success(await chunk_service.update_strategy(session, user.id, kb_id, data))


@router.post("/{kb_id}/reindex")
async def reindex(
    kb_id: int, data: ChunkRequest,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """全量重切重建索引（M3.3）：清空旧切片后重新切割向量化。"""
    return success(await chunk_service.chunk_and_index(session, user.id, kb_id, data, full=True), "重建完成")


@router.post("/{kb_id}/incremental-index")
async def incremental_index(
    kb_id: int, data: ChunkRequest,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """仅增量索引新增/未索引文档（M3.4）。"""
    return success(await chunk_service.incremental_index(session, user.id, kb_id, data), "增量完成")
