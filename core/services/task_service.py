"""
统一异步任务服务（所有向量/AI 耗时任务共享）

整体思路：
    提供任务的创建、进度更新、查询能力，作为解析/切割/图谱抽取/人物生成等后台任务的
    统一进度落库点，使仪表盘与前端全局轮询只需读这一张表。

关键点：
    1. create_task 在请求会话内提交，返回带自增 id 的任务，供后台协程回写进度。
    2. update_task_progress 自开 SessionLocal，供后台任务（无请求会话）安全更新。
    3. list_tasks/list_running 按 owner 隔离；_out 统一出参结构，前端直接消费。

实现逻辑：
    基于 AsyncSession 的 select/get；后台更新走独立会话避免会话跨协程复用。
"""
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.async_task import AsyncTask
from db import SessionLocal


async def create_task(
    session: AsyncSession, owner_id: int, type: str, name: str,
    novel_id: int | None = None, kb_id: int | None = None,
    doc_id: int | None = None, target_id: int | None = None,
    extra: dict | None = None,
) -> AsyncTask:
    """创建一条异步任务（请求会话内提交）。"""
    t = AsyncTask(
        owner_id=owner_id, type=type, name=name,
        novel_id=novel_id, kb_id=kb_id, doc_id=doc_id, target_id=target_id,
        extra=extra or {}, status="running", stage="pending",
    )
    session.add(t)
    await session.commit()
    await session.refresh(t)
    return t


async def update_task_progress(
    task_id: int, stage: str | None = None, progress: int | None = None,
    status: str | None = None, error: str | None = None,
    started_at: datetime | None = None, finished_at: datetime | None = None,
    tokens_in: int | None = None, tokens_out: int | None = None,
    **extra,
) -> AsyncTask | None:
    """后台任务进度回写：独立会话更新，避免持有请求会话。"""
    async with SessionLocal() as session:
        t = await session.get(AsyncTask, task_id)
        if not t:
            return None
        if stage is not None:
            t.stage = stage
        if progress is not None:
            t.progress = progress
        if status is not None:
            t.status = status
        if error is not None:
            t.error = error
        if started_at is not None:
            t.started_at = started_at
        if finished_at is not None:
            t.finished_at = finished_at
        if tokens_in is not None:
            t.tokens_in = tokens_in
        if tokens_out is not None:
            t.tokens_out = tokens_out
        if extra:
            t.extra = {**(t.extra or {}), **extra}
        await session.commit()
        return t


async def get_task(session: AsyncSession, task_id: int) -> AsyncTask | None:
    """按 id 取任务（调用方负责归属校验）。"""
    return await session.get(AsyncTask, task_id)

async def list_tasks(
    session: AsyncSession, owner_id: int,
    status: str | None = None, type: str | None = None, limit: int = 50,
) -> list[dict]:
    """任务列表：按 owner 隔离，可筛选状态/类型，倒序取最近。"""
    q = select(AsyncTask).where(AsyncTask.owner_id == owner_id)
    if status:
        q = q.where(AsyncTask.status == status)
    if type:
        q = q.where(AsyncTask.type == type)
    q = q.order_by(AsyncTask.id.desc()).limit(limit)
    rows = (await session.execute(q)).scalars().all()
    return [_out(t) for t in rows]


async def list_running(session: AsyncSession, owner_id: int) -> list[dict]:
    """进行中任务列表（前端全局轮询用）。"""
    return await list_tasks(session, owner_id, status="running")


async def query_tasks(
    session: AsyncSession, owner_id: int,
    status: str | None = None, type: str | None = None,
    novel_name: str | None = None, completed: bool | None = None,
    page: int = 1, page_size: int = 20,
) -> dict:
    """分页 + 条件查询统一异步任务（按 owner 隔离）。

    条件：
        1. status 精确匹配（running/success/failed/cancelled）。
        2. type 精确匹配（parse/chunk/graph/character）。
        3. completed 布尔：True=已结束(非 running)，False=进行中。
        4. novel_name 小说名模糊匹配（ILIKE %kw%），按 novel_id 关联 story_novel。
    返回 {items, total, page, page_size}，供仪表盘与前端分页展示。
    """
    from models.novel_content import Novel
    base = select(AsyncTask).where(AsyncTask.owner_id == owner_id)
    if status:
        base = base.where(AsyncTask.status == status)
    if type:
        base = base.where(AsyncTask.type == type)
    if completed is not None:
        base = base.where(AsyncTask.status != "running" if completed else AsyncTask.status == "running")
    if novel_name:
        base = base.join(Novel, Novel.id == AsyncTask.novel_id).where(Novel.name.ilike(f"%{novel_name}%"))
    total = (await session.execute(base.with_only_columns(func.count()))).scalar() or 0
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    rows = (await session.execute(
        base.order_by(AsyncTask.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return {"items": [_out(t) for t in rows], "total": total, "page": page, "page_size": page_size}


def _out(t: AsyncTask) -> dict:
    """任务出参：统一结构，前端直接消费。"""
    return {
        "id": t.id, "type": t.type, "name": t.name,
        "novel_id": t.novel_id, "kb_id": t.kb_id,
        "doc_id": t.doc_id, "target_id": t.target_id,
        "stage": t.stage, "progress": t.progress, "status": t.status,
        "error": t.error,
        "tokens_in": t.tokens_in or 0, "tokens_out": t.tokens_out or 0,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "extra": t.extra or {},
    }


async def record_llm_usage(
    owner_id: int, config_id: int | None, model: str,
    task_type: str, tokens_in: int, tokens_out: int,
) -> None:
    """写入一条 LLM 调用用量记录到 story_llm_usage 表（独立会话）。

    整体思路：
        每次 LLM chat/embed 调用完成后写入用量，供仪表盘 Token 计费和模型占比统计。
    关键点：
        1. 独立 SessionLocal，避免与业务会话冲突。
        2. 写入失败仅记日志，不影响主业务。
    实现逻辑：
        开独立会话 → add LLMUsage → commit。
    """
    if not tokens_in and not tokens_out:
        return
    try:
        from models.llm_config import LLMUsage
        async with SessionLocal() as session:
            session.add(LLMUsage(
                owner_id=owner_id, config_id=config_id, model=model,
                task_type=task_type, tokens_in=tokens_in, tokens_out=tokens_out,
            ))
            await session.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("LLM 用量记录失败: %s", e)
