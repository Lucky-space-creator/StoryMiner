"""
解析服务（M1 上传/异步解析/进度推送）

整体思路：
    处理小说上传：落盘、去重、建文档与解析任务，后台异步切章并实时推送进度（SSE）。

关键点：
    1. 上传校验：扩展名与大小（TXT≤50MB、其他≤200MB、章节≤5000）。
    2. 去重：按文件 sha256 检测同小说重复上传（M1.8）。
    3. 自动为小说建立默认知识库「正文库」以写入 document.kb_id（满足表约束）。
    4. 后台任务：读文本→正则切章→批量入库→更新进度与状态。
    5. 进度通过内存发布订阅推送给 SSE 端点。

实现逻辑：
    handle_upload 校验并落库后，用 BackgroundTasks 调度 _run_parse；
    SSE 端点 subscribe 队列，按 stage/progress/status 推送事件。
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from fastapi import UploadFile, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from config import MAX_TXT_BYTES, MAX_OTHER_BYTES, MAX_CHAPTERS, ALLOWED_EXT
from common.exceptions import BizError
from common.threadpool import run_in_thread
from models.novel_content import Document, ParseTask, Chapter
from repositories import novel_repo
from services import task_service
from parsers.chapter_splitter import split_text
from storage import save as save_file, read as read_file, delete as delete_file
from db import SessionLocal

logger = logging.getLogger(__name__)

# 内存进度订阅：task_id -> [queue, ...]
_subscribers: dict[int, list[asyncio.Queue]] = {}


async def _publish(task_id: int, event: dict) -> None:
    """向订阅者推送进度事件。"""
    for q in _subscribers.get(task_id, []):
        await q.put(event)


async def subscribe(task_id: int) -> asyncio.Queue:
    """注册 SSE 订阅队列。"""
    q = asyncio.Queue()
    _subscribers.setdefault(task_id, []).append(q)
    return q


def _ext(name: str) -> str:
    """取小写扩展名。"""
    return os.path.splitext(name or "")[1].lower()


def _sse(event: dict) -> str:
    """格式化为 SSE data 帧。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def handle_upload(
    session: AsyncSession, owner_id: int, novel_id: int,
    file: UploadFile, background: BackgroundTasks, kb_id: int | None = None,
) -> dict:
    """处理小说上传：校验→落盘→去重→建文档/任务→调度后台解析。

    方案A：文档仅归属小说（kb_id 置空），不再强绑默认知识库；纳入知识库由链接表在建/构建时建立。
    kb_id 参数保留以兼容旧调用，当前不再使用。
    """
    novel = await novel_repo.get_novel(session, owner_id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")
    ext = _ext(file.filename)
    if ext not in ALLOWED_EXT:
        raise BizError(400, f"不支持的格式：{ext or '未知'}（仅支持 {','.join(sorted(ALLOWED_EXT))}）")
    data = await file.read()
    limit = MAX_TXT_BYTES if ext == ".txt" else MAX_OTHER_BYTES
    if len(data) > limit:
        raise BizError(400, f"文件超过上限（{'50MB' if ext == '.txt' else '200MB'}）")
    # 去重：sha256 与文件写入为同步阻塞 IO，放到线程池避免阻塞事件循环影响其他用户请求
    path, digest = await run_in_thread(save_file, owner_id, novel_id, file.filename or "upload", data)
    dup = await novel_repo.find_document_by_hash(session, novel_id, digest)
    if dup:
        # 已存在则清理刚落盘对象，返回冲突
        await delete_file(path)
        raise BizError(409, "该文件已上传过（内容重复），已忽略")
    # 方案A：文档归属小说，kb_id 置空；纳入知识库在建/构建时通过链接表建立关联。
    doc = Document(
        kb_id=None, novel_id=novel_id, owner_id=owner_id,
        name=os.path.basename(file.filename or "upload"),
        doc_type=ext.lstrip("."), object_key=path,
        file_hash=digest, status="pending",
    )
    await novel_repo.create_document(session, doc)
    task = ParseTask(
        owner_id=owner_id, novel_id=novel_id, doc_id=doc.id,
        stage="pending", progress=0, status="running",
    )
    await novel_repo.create_parse_task(session, task)
    await session.commit()
    await session.refresh(doc)
    await session.refresh(task)
    # 统一异步任务：镜像一条 AsyncTask 供仪表盘/全局轮询展示解析进度
    async_task = await task_service.create_task(
        session, owner_id, "parse", f"小说{novel.name}-文档解析",
        novel_id=novel_id, doc_id=doc.id, extra={"parse_task_id": task.id},
    )
    task.extra = {**(task.extra or {}), "async_task_id": async_task.id}
    await session.commit()
    # 调度后台解析
    background.add_task(_run_parse, task.id, path, owner_id, novel_id, doc.id)
    return {"task_id": task.id, "doc_id": doc.id, "name": doc.name, "status": "running"}


async def _run_parse(task_id: int, path: str, owner_id: int, novel_id: int, doc_id: int) -> None:
    """后台解析：读文本→切章→入库→更新进度（独立会话）。"""
    async with SessionLocal() as session:
        task = await novel_repo.get_parse_task(session, task_id)
        doc = await novel_repo.get_document(session, doc_id)
        async_task_id = (task.extra or {}).get("async_task_id")
        try:
            await _publish(task_id, {"stage": "parsing", "progress": 10, "status": "running", "payload": {}})
            task.stage, task.progress = "parsing", 10
            task.started_at = datetime.now(timezone.utc)
            if async_task_id:
                await task_service.update_task_progress(async_task_id, stage="parsing", progress=10, status="running", started_at=task.started_at)
            # 读取文本（多格式：TXT 直接解码，其他按忽略错误解码，阶段1仅保证可运行）
            # read_file 为同步阻塞 IO（本地文件读取），放线程池避免阻塞事件循环
            raw = await run_in_thread(read_file, path)
            text = raw.decode("utf-8", errors="ignore")
            await _publish(task_id, {"stage": "splitting", "progress": 40, "status": "running", "payload": {}})
            task.stage, task.progress = "splitting", 40
            if async_task_id:
                await task_service.update_task_progress(async_task_id, stage="splitting", progress=40)
            # 切章为 CPU 密集型，放到独立线程执行，避免阻塞主事件循环影响其他用户请求
            chapters = await run_in_thread(split_text, text)
            if len(chapters) > MAX_CHAPTERS:
                chapters = chapters[:MAX_CHAPTERS]
            objs = [
                Chapter(
                    novel_id=novel_id, owner_id=owner_id,
                    title=ch["title"], chapter_no=i,
                    content=ch["content"], word_count=ch["word_count"],
                    char_start=ch["char_start"], char_end=ch["char_end"],
                )
                for i, ch in enumerate(chapters, start=1)
            ]
            await novel_repo.add_chapters(session, objs)
            doc.status, doc.word_count = "done", sum(c["word_count"] for c in chapters)
            task.stage, task.progress, task.status = "done", 100, "success"
            task.finished_at = datetime.now(timezone.utc)
            await session.commit()
            if async_task_id:
                await task_service.update_task_progress(async_task_id, stage="done", progress=100, status="success", finished_at=task.finished_at)
            await _publish(task_id, {"stage": "done", "progress": 100, "status": "success",
                                     "payload": {"chapter_count": len(objs)}})
            # 解析成功后触发 AI 概括生成（异步，不阻塞当前任务）
            await _trigger_ai_summary(owner_id, novel_id, novel.name)
        except Exception as e:  # 解析失败：回写状态并推送
            from common.task_errors import to_user_error
            user_msg = to_user_error(e)
            await session.rollback()
            task.status, task.stage, task.error = "failed", "failed", user_msg
            task.finished_at = datetime.now(timezone.utc)
            if doc:
                doc.status, doc.error = "failed", user_msg
            await session.commit()
            if async_task_id:
                await task_service.update_task_progress(async_task_id, stage="failed", progress=task.progress, status="failed", error=user_msg, finished_at=task.finished_at)
            await _publish(task_id, {"stage": "failed", "progress": task.progress,
                                     "status": "failed", "payload": {"error": user_msg}})


async def list_tasks(session: AsyncSession, owner_id: int) -> list[dict]:
    """当前用户解析任务列表。"""
    items = await novel_repo.list_parse_tasks(session, owner_id)
    return [{
        "id": t.id, "novel_id": t.novel_id, "doc_id": t.doc_id,
        "stage": t.stage, "progress": t.progress, "status": t.status,
        "error": t.error,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in items]


async def retry_task(session: AsyncSession, owner_id: int, task_id: int, background: BackgroundTasks) -> dict:
    """失败任务重试（M14.2）：重置状态并重新调度后台解析。"""
    task = await novel_repo.get_parse_task(session, task_id)
    if not task or task.owner_id != owner_id:
        raise BizError(404, "任务不存在")
    if task.status != "failed":
        raise BizError(400, "仅失败任务可重试")
    doc = await novel_repo.get_document(session, task.doc_id) if task.doc_id else None
    if not doc:
        raise BizError(400, "关联文档已不存在，无法重试")
    # 重置任务与文档状态后重新调度
    task.stage, task.progress, task.status = "pending", 0, "running"
    task.error, task.started_at, task.finished_at = None, None, None
    doc.status, doc.error = "pending", None
    await session.commit()
    background.add_task(_run_parse, task.id, doc.object_key, owner_id, task.novel_id, doc.id)
    return {
        "id": task.id, "novel_id": task.novel_id, "doc_id": task.doc_id,
        "stage": task.stage, "progress": task.progress, "status": task.status,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


async def progress_stream(session: AsyncSession, task_id: int, owner_id: int):
    """生成 SSE 进度事件流（异步生成器）。"""
    task = await novel_repo.get_parse_task(session, task_id)
    if not task or task.owner_id != owner_id:
        raise BizError(404, "任务不存在")
    q = await subscribe(task_id)
    # 先推当前状态
    yield _sse({"stage": task.stage, "progress": task.progress, "status": task.status, "payload": {}})
    if task.status in ("success", "failed"):
        return
    while True:
        ev = await q.get()
        yield _sse(ev)
        if ev["status"] in ("success", "failed"):
            break


async def _trigger_ai_summary(owner_id: int, novel_id: int, novel_name: str) -> None:
    """解析完成后触发 AI 概括生成（异步，不阻塞解析任务，不阻塞用户请求）。

    整体思路：
        在统一异步任务表中创建 summary 类型任务，并创建后台协程调用 novel_service.generate_ai_summary。
    关键点：
        1. 失败不影响解析已完成的状态，仅记录到 summary 任务。
        2. 使用 asyncio.create_task 真正异步执行，不阻塞 _run_parse 返回，也不阻塞用户后续请求。
        3. 给后台任务挂 done 回调，确保异常被记录而非冒泡到事件循环（避免「Task exception was never retrieved」告警）。
        4. create_task 本身异常（如创建任务记录失败）只记日志，不影响已完成的解析。
    实现逻辑：
        开独立会话写任务记录 → create_task 调度后台协程 → 挂 done 回调兜底异常。
    """
    try:
        # 用独立会话创建 summary 异步任务记录，避免与解析任务的会话交叉
        async with SessionLocal() as session:
            summary_task = await task_service.create_task(
                session, owner_id, "summary", f"小说{novel_name}-AI概括生成",
                novel_id=novel_id, extra={"novel_name": novel_name},
            )
            summary_task_id = summary_task.id
        # 真正异步执行，不等待结果（延迟导入 novel_service 避免 services 包循环依赖）
        from services import novel_service as _novel_service
        task = asyncio.create_task(
            _novel_service.generate_ai_summary(
                novel_id=novel_id, owner_id=owner_id,
                novel_name=novel_name, async_task_id=summary_task_id,
            )
        )
        # done 回调：捕获并记录任务异常，避免「exception was never retrieved」告警
        task.add_done_callback(_on_bg_task_done)
    except Exception as e:  # noqa: BLE001
        logger.warning("触发 AI 概括生成失败：小说 %s，错误：%s", novel_id, e)


def _on_bg_task_done(task: asyncio.Task) -> None:
    """后台任务完成回调：捕获并记录异常，避免事件循环告警。

    整体思路：
        任务结束后检查是否抛异常，若有则记日志（异常已在 generate_ai_summary 内部处理并写任务进度）。
    关键点：
        1. 调 task.exception() 会消费异常，避免 asyncio 在 GC 时再次打印。
        2. CancelledError 视为正常取消，不打 error 日志。
    实现逻辑：
        if not cancelled → exception 不为 None → logger.error 记录。
    """
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.error("后台 AI 概括任务异常退出：%s", exc, exc_info=exc)
