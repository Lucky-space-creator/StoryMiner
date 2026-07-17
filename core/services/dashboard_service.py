"""
仪表盘聚合服务（M14 数据展示）

整体思路：
    聚合资源计数、Token 消费、趋势与模型占比，统一按 owner_id 隔离，返回前端 Dashboard 所需结构。

关键点：
    1. 资源计数分别查各表（过滤逻辑删除），向量数等同切片数（每切片均有向量）。
    2. Token 用量对 LLMUsage 做 sum/count 聚合；趋势按日/周分组并补零保证连续。
    3. 模型占比按 model 分组统计调用次数与费用。

实现逻辑：
    基于 async session 的 select/func 聚合；趋势用 date_trunc 思路（Python 侧分桶）后补零填充区间。
"""
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.novel_content import Novel, KnowledgeBase, ParseTask
from models.chunk import Chunk
from models.graph import Entity
from models.llm_config import LLMUsage


async def get_stats(session: AsyncSession, owner_id: int) -> dict:
    """资源统计卡片（M14.6）：小说/知识库/切片/实体/向量数。"""
    novels = (await session.execute(
        select(func.count()).select_from(Novel).where(Novel.owner_id == owner_id, Novel.deleted_at.is_(None))
    )).scalar() or 0
    kbs = (await session.execute(
        select(func.count()).select_from(KnowledgeBase).where(
            KnowledgeBase.owner_id == owner_id, KnowledgeBase.deleted_at.is_(None))
    )).scalar() or 0
    chunks = (await session.execute(
        select(func.count()).select_from(Chunk).where(Chunk.owner_id == owner_id)
    )).scalar() or 0
    entities = (await session.execute(
        select(func.count()).select_from(Entity).where(Entity.owner_id == owner_id, Entity.deleted_at.is_(None))
    )).scalar() or 0
    return {
        "novels": int(novels), "knowledgeBases": int(kbs),
        "chunks": int(chunks), "entities": int(entities), "vectors": int(chunks),
    }


async def get_token_usage(session: AsyncSession, owner_id: int) -> dict:
    """Token 消费总览（M14.3）：累计输入/输出、调用次数、费用。"""
    row = (await session.execute(
        select(
            func.coalesce(func.sum(LLMUsage.tokens_in), 0),
            func.coalesce(func.sum(LLMUsage.tokens_out), 0),
            func.count(),
            func.coalesce(func.sum(LLMUsage.cost), 0),
        ).select_from(LLMUsage).where(LLMUsage.owner_id == owner_id)
    )).first()
    return {
        "tokensIn": int(row[0] or 0),
        "tokensOut": int(row[1] or 0),
        "calls": int(row[2] or 0),
        "cost": float(row[3] or 0),
    }


def _date_key(dt: datetime, weekly: bool) -> str:
    """日期分桶键：日=YYYY-MM-DD，周=该周周一 YYYY-MM-DD。"""
    if weekly:
        monday = dt - timedelta(days=dt.weekday())
        return monday.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d")


async def get_token_trend(session: AsyncSession, owner_id: int, period: str = "day") -> list[dict]:
    """消费趋势（M14.4）：按日/周聚合 token 总量，补零保证连续区间。"""
    weekly = period == "week"
    n = 8 if weekly else 14
    now = datetime.now(timezone.utc)
    buckets: list[str] = []
    bucket_set: set[str] = set()
    if weekly:
        this_monday = now - timedelta(days=now.weekday())
        for i in range(n - 1, -1, -1):
            key = (this_monday - timedelta(weeks=i)).strftime("%Y-%m-%d")
            buckets.append(key)
            bucket_set.add(key)
    else:
        for i in range(n - 1, -1, -1):
            key = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            buckets.append(key)
            bucket_set.add(key)

    start = datetime.strptime(buckets[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    rows = (await session.execute(
        select(LLMUsage.created_at, LLMUsage.tokens_in, LLMUsage.tokens_out)
        .select_from(LLMUsage)
        .where(LLMUsage.owner_id == owner_id, LLMUsage.created_at >= start)
    )).all()
    agg = defaultdict(int)
    for created_at, tin, tout in rows:
        dt = created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        key = _date_key(dt, weekly)
        if key in bucket_set:
            agg[key] += int(tin or 0) + int(tout or 0)
    return [{"date": b, "tokens": agg.get(b, 0)} for b in buckets]


async def get_model_stats(session: AsyncSession, owner_id: int) -> list[dict]:
    """模型维度统计（M14.5）：各模型调用次数与费用占比。"""
    rows = (await session.execute(
        select(LLMUsage.model, func.count(), func.coalesce(func.sum(LLMUsage.cost), 0))
        .select_from(LLMUsage)
        .where(LLMUsage.owner_id == owner_id)
        .group_by(LLMUsage.model)
        .order_by(func.count().desc())
    )).all()
    return [
        {"name": r[0] or "unknown", "calls": int(r[1] or 0), "cost": float(r[2] or 0)}
        for r in rows
    ]


async def get_task_overview(session: AsyncSession, owner_id: int) -> dict:
    """异步任务进度总览（M14.1）：汇总当前用户解析/切割任务及状态计数。"""
    items = (await session.execute(
        select(ParseTask).where(ParseTask.owner_id == owner_id).order_by(ParseTask.id.desc())
    )).scalars().all()
    tasks = [{
        "id": t.id, "novel_id": t.novel_id, "doc_id": t.doc_id,
        "stage": t.stage, "progress": t.progress, "status": t.status,
        "error": t.error,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in items]
    summary = {
        "total": len(tasks),
        "running": sum(1 for t in tasks if t["status"] == "running"),
        "success": sum(1 for t in tasks if t["status"] == "success"),
        "failed": sum(1 for t in tasks if t["status"] == "failed"),
    }
    return {"tasks": tasks, "summary": summary}
