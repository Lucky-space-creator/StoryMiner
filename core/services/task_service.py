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

from sqlalchemy import select, func, insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.async_task import AsyncTask
from db import SessionLocal

import logging

logger = logging.getLogger(__name__)


async def create_task(
    session: AsyncSession, owner_id: int, type: str, name: str,
    novel_id: int | None = None, kb_id: int | None = None,
    doc_id: int | None = None, target_id: int | None = None,
    extra: dict | None = None,
    estimated_duration_minutes: int | None = None,
    is_long_task: bool = False,
    estimated_complete_at: str | None = None,
) -> AsyncTask:
    """
    创建一条异步任务（请求会话内提交）。

    V19：支持长耗时任务预估参数，新任务默认写入 is_long_task / estimated_duration_minutes
    / estimated_complete_at 字段，供前端差异化展示（Dashboard 短任务 / 长任务中心）。
    """
    from datetime import datetime as dt
    etc = None
    if estimated_complete_at:
        try:
            etc = dt.fromisoformat(estimated_complete_at)
        except (ValueError, TypeError):
            etc = None
    t = AsyncTask(
        owner_id=owner_id, type=type, name=name,
        novel_id=novel_id, kb_id=kb_id, doc_id=doc_id, target_id=target_id,
        extra=extra or {}, status="running", stage="pending",
        estimated_duration_minutes=estimated_duration_minutes,
        is_long_task=is_long_task,
        estimated_complete_at=etc,
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
    usage_info: dict | None = None,
    **extra,
) -> AsyncTask | None:
    """后台任务进度回写：独立会话更新，避免持有请求会话。
    任务进入终态（success/failed/cancelled）时，统一将累积 token 用量写入 story_llm_usage 表。
    不在执行过程中逐次写入，减少 DB IO 次数。
    """
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
        # 任务进入终态时，统一将累积的 token 用量写入 story_llm_usage 表
        # 复用同一会话（不在执行过程中逐次写入，减少 DB IO；P2-18 避免嵌套再开连接）
        if status in ("success", "failed", "cancelled") and (tokens_in or tokens_out) and usage_info:
            try:
                await record_llm_usage(
                    owner_id=t.owner_id,
                    config_id=usage_info.get("config_id"),
                    model=usage_info.get("model") or "",
                    task_type=usage_info.get("task_type") or "",
                    tokens_in=tokens_in or 0,
                    tokens_out=tokens_out or 0,
                    task_id=task_id,
                    session=session,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("LLM 用量统一写入失败 task=%s: %s", task_id, e)
        # 任务字段更新与用量记录在同一会话、一次 commit 提交（P2-18：仅占用一条连接）
        await session.commit()
        # V20：进度回写后向该 owner 的 SSE 订阅者广播快照，替代前端定时轮询
        from common.task_pubsub import publish
        try:
            publish(t.owner_id, _out(t))
        except Exception as e:  # noqa: BLE001
            logger.warning("任务进度广播失败 task=%s: %s", task_id, e)
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
    is_long_task: bool | None = None,
    page: int = 1, page_size: int = 20,
) -> dict:
    """分页 + 条件查询统一异步任务（按 owner 隔离）。

    条件：
        1. status 精确匹配（running/success/failed/cancelled）。
        2. type 精确匹配（parse/chunk/graph/character）。
        3. completed 布尔：True=已结束(非 running)，False=进行中。
        4. novel_name 小说名模糊匹配（ILIKE %kw%），按 novel_id 关联 story_novel。
        5. V19 is_long_task 布尔：True=长任务，False=短任务，None=不过滤。
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
    if is_long_task is not None:
        base = base.where(AsyncTask.is_long_task == is_long_task)
    total = (await session.execute(base.with_only_columns(func.count()))).scalar() or 0
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    rows = (await session.execute(
        base.order_by(AsyncTask.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return {"items": [_out(t) for t in rows], "total": total, "page": page, "page_size": page_size}


def _out(t: AsyncTask) -> dict:
    """任务出参：统一结构，前端直接消费。V19 新增预估耗时、长任务标记、预计完成时刻。"""
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
        "estimated_duration_minutes": t.estimated_duration_minutes,
        "is_long_task": t.is_long_task if hasattr(t, 'is_long_task') else False,
        "estimated_complete_at": t.estimated_complete_at.isoformat() if t.estimated_complete_at else None,
        "extra": t.extra or {},
    }


async def record_llm_usage(
    owner_id: int, config_id: int | None, model: str,
    task_type: str, tokens_in: int, tokens_out: int,
    task_id: int | None = None, session=None,
) -> None:
    """写入一条 LLM 调用用量记录到 story_llm_usage 表。

    整体思路：每次 LLM chat/embed 调用完成后写入用量，供仪表盘 Token 计费和模型占比统计。
    关键点：
        1. 支持传入外部 session（如 update_task_progress 的同一会话）以复用连接（P2-18）；
           未传入时自开 SessionLocal，避免与业务会话冲突。
        2. 幂等约束（P2-17）：以 task_id 建立唯一索引，使用 INSERT ... ON CONFLICT DO NOTHING，
           补偿/重试写入同 task_id 时不会重复计费。
        3. model / task_type 缺省兜底为 "unknown"，避免写入空桶污染仪表盘统计（P2-19）。
        4. 写入失败仅记日志，不影响主业务。
    实现逻辑：拼接 insert 语句（含冲突忽略）→ 执行 → flush（外部会话）或 commit（自管会话）。
    """
    if not tokens_in and not tokens_out:
        return
    own = session
    close_own = False
    try:
        if own is None:
            own = SessionLocal()
            close_own = True
        from models.llm_config import LLMUsage
        stmt = insert(LLMUsage).values(
            owner_id=owner_id, config_id=config_id,
            model=(model or "unknown"), task_type=(task_type or "unknown"),
            tokens_in=tokens_in, tokens_out=tokens_out, task_id=task_id,
        ).on_conflict_do_nothing(index_elements=["task_id"])
        await own.execute(stmt)
        if close_own:
            await own.commit()
        else:
            await own.flush()
    except Exception as e:
        logger.warning("LLM 用量记录失败 task=%s: %s", task_id, e)
    finally:
        if close_own and own is not None:
            await own.close()
