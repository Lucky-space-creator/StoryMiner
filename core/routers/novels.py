"""
小说与内容路由（/api/v1/novels）

整体思路：
    暴露 M1 全部接口：小说 CRUD、上传解析、章节列表/校正/拆分/合并、解析任务与进度。

关键点：
    1. 所有写接口依赖 get_current_user 取得 owner_id，实现数据隔离。
    2. 上传返回解析任务，进度经 SSE（/parse-tasks/{id}/progress）推送。
    3. 响应统一 {code,msg,data}；分页返回 {list,total,page,size}。

实现逻辑：
    委托 novel_service / parse_service；SSE 用 StreamingResponse。
"""
from fastapi import APIRouter, Depends, UploadFile, BackgroundTasks, Query

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success, paginate
from common.exceptions import BizError
from schemas.novel import NovelCreate, NovelUpdate, ChapterCorrect, ChapterSplit, ChapterMerge
from services import novel_service, parse_service, kb_service
from repositories import novel_repo

router = APIRouter(prefix="/novels", tags=["novels"])


@router.post("")
async def create_novel(
    data: NovelCreate,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """创建小说空间（M1.1）。"""
    novel = await novel_service.create_novel(session, user.id, data)
    return success(novel_service.novel_out(novel), "创建成功")


@router.get("")
async def list_novels(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """小说列表（分页，按 owner 隔离）。"""
    items, total = await novel_service.list_novels(session, user.id, page, size)
    return success(paginate(items, total, page, size))


@router.get("/{novel_id}")
async def get_novel(novel_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """小说详情 + 元信息 + 章节数（M1.5）。"""
    return success(await novel_service.get_novel(session, user.id, novel_id))


@router.put("/{novel_id}")
async def update_novel(
    novel_id: int, data: NovelUpdate,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """更新元信息/封面（M1.6）。"""
    return success(await novel_service.update_novel(session, user.id, novel_id, data))


@router.delete("/{novel_id}")
async def delete_novel(novel_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """删除小说（逻辑删除）。"""
    await novel_service.delete_novel(session, user.id, novel_id)
    return success(msg="已删除")


@router.post("/{novel_id}/upload")
async def upload(
    novel_id: int, files: list[UploadFile],
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """上传文档（方案A：支持多文件）并逐个触发异步解析，文档归属小说。"""
    if not await novel_repo.get_novel(session, user.id, novel_id):
        raise BizError(404, "小说不存在")
    results = []
    for f in files:
        try:
            r = await parse_service.handle_upload(session, user.id, novel_id, f, background_tasks)
            results.append(r)
        except BizError as e:
            results.append({"name": f.filename, "error": e.msg})
    return success(results, "上传完成")


@router.get("/{novel_id}/documents")
async def novel_documents(
    novel_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=500),
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """小说下文档列表（方案A：文档归属小说，供详情页 CRUD 与构建选文件）。"""
    if not await novel_repo.get_novel(session, user.id, novel_id):
        raise BizError(404, "小说不存在")
    items, total = await novel_repo.list_documents_by_novel(session, novel_id, page, size)
    return success(paginate([kb_service.doc_out(d) for d in items], total, page, size))


@router.get("/{novel_id}/chapters")
async def chapters(
    novel_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, description="标题模糊搜索"),
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """章节列表（M1.3）：分页 + 标题查询。"""
    data = await novel_service.list_chapters(session, user.id, novel_id, page, size, q)
    return success(paginate(data["list"], data["total"], data["page"], data["size"]))


@router.get("/chapters/{chapter_id}")
async def get_chapter(chapter_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """章节详情（含正文，M1.3 查看/编辑前读取）。"""
    return success(await novel_service.get_chapter_detail(session, user.id, chapter_id))


@router.put("/chapters/{chapter_id}")
async def correct_chapter(
    chapter_id: int, data: ChapterCorrect,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """章节校正：重命名/内容编辑（M1.4）。"""
    return success(await novel_service.correct_chapter(session, user.id, chapter_id, data))


@router.post("/chapters/{chapter_id}/split")
async def split_chapter(
    chapter_id: int, data: ChapterSplit,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """按字符偏移拆分章节（M1.4）。"""
    return success(await novel_service.split_chapter(session, user.id, chapter_id, data))


@router.post("/chapters/merge")
async def merge_chapters(
    data: ChapterMerge,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """合并多个章节（M1.4）。"""
    return success(await novel_service.merge_chapters(session, user.id, data.chapter_ids))
