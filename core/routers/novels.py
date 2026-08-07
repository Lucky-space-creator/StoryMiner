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
from sqlalchemy import select

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success, paginate
from common.exceptions import BizError
from common import task_queue
from config import ANALYSIS_MODE_DEFAULT
from schemas.novel import NovelCreate, NovelUpdate, ChapterCorrect, ChapterSplit, ChapterMerge
from services import (
    novel_service, parse_service, kb_service, chapter_analysis_service,
    task_service, fast_analysis_service, task_estimation,
)
from repositories import novel_repo
from models.analysis_summary import StoryAnalysisSummary

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


# ---------------------------------------------------------------------------
# 章节解析（LLM 驱动的章节结构化分析）
# ---------------------------------------------------------------------------
@router.post("/{novel_id}/chapters/analyze")
async def trigger_chapter_analysis(
    novel_id: int,
    background_tasks: BackgroundTasks,
    mode: str = Query(ANALYSIS_MODE_DEFAULT, pattern="^(turbo|deep)$"),
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """触发章节解析异步任务：LLM 分析小说整体结构 + 逐章生成摘要/事件/人物等信息。

    整体思路：
        创建 chapter_analysis 类型的异步任务，将 LLM 分析工作交给后台协程执行，
        前端通过 /tasks/{task_id} 或仪表盘轮询进度。

    关键点：
        1. 需小说下有章节数据，否则返回错误提示。
        2. 同一小说可多次触发，每次覆盖之前的分析结果（幂等写入 extra 字段）。
        3. 返回 task_id 供前端跳转仪表盘查看进度。
        4. mode=turbo：极速摘要（情节概览）；mode=deep：深度全量逐章解析。
        5. deep 模式统一标记为长任务，前端直接指引到「长任务中心」查看进度。
    """
    novel = await novel_repo.get_novel(session, user.id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")
    # V19：根据小说字数与处理模式预估任务耗时，区分长短任务
    # Novel 模型无 word_count 字段，从 extra 中获取或默认 0
    _wc = (novel.extra or {}).get("word_count", 0) if novel.extra else 0
    est = task_estimation.estimate_task_duration(_wc, mode)
    task = await task_service.create_task(
        session, user.id, type="chapter_analysis",
        name=f"章节解析·{novel.name}", novel_id=novel_id,
        extra={"mode": mode},
        estimated_duration_minutes=est["estimated_minutes"],
        is_long_task=est["is_long_task"],
        estimated_complete_at=est["estimated_complete_at"],
    )
    if mode == "turbo":
        # 极速：单次/少量大上下文调用产出情节概览摘要
        task_queue.submit(
            fast_analysis_service.run_turbo, novel_id, user.id, "chapter", task.id)
        return success({
            "task_id": task.id, "novel_id": novel_id, "mode": "turbo",
            "estimated_minutes": est["estimated_minutes"],
            "is_long_task": est["is_long_task"],
            "estimated_complete_at": est["estimated_complete_at"],
        }, "极速章节解析任务已启动")
    # 提交后台任务（入全局串行队列，逐一执行；排队中前端显示「排队中」）
    task_queue.submit(
        chapter_analysis_service.analyze_chapters,
        novel_id=novel_id, owner_id=user.id,
        novel_name=novel.name, summary=novel.summary or "",
        async_task_id=task.id,
    )
    return success({
        "task_id": task.id, "novel_id": novel_id, "mode": "deep",
        "estimated_minutes": est["estimated_minutes"],
        "is_long_task": est["is_long_task"],
        "estimated_complete_at": est["estimated_complete_at"],
    }, "章节解析任务已启动")


@router.get("/{novel_id}/analysis-summary")
async def get_analysis_summary(
    novel_id: int,
    analysis_type: str = Query("character", pattern="^(character|chapter|graph)$"),
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """读取极速模式摘要（按小说 + 类型），供前端极速结果展示。

    返回 {exists, content, fmt, updated_at}；深度模式未产出摘要时 exists=false。
    """
    novel = await novel_repo.get_novel(session, user.id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")
    row = (await session.execute(
        select(StoryAnalysisSummary).where(
            StoryAnalysisSummary.novel_id == novel_id,
            StoryAnalysisSummary.analysis_type == analysis_type))).scalars().first()
    if not row:
        return success({"exists": False, "content": "", "fmt": "markdown", "updated_at": None})
    return success({
        "exists": True,
        "content": row.content,
        "fmt": row.fmt,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    })


@router.get("/{novel_id}/chapter-analysis")
async def get_chapter_analysis(
    novel_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """获取小说章节分析结果：包含整体结构分析 + 各章摘要/事件/人物等。

    返回：
        {structure: {...}, chapters: [{id, title, chapter_no, analysis: {...}}, ...]}
    """
    result = await chapter_analysis_service.get_chapter_analysis(session, user.id, novel_id)
    return success(result)
