"""
仪表盘路由（/api/v1/dashboard，M14 数据展示）

整体思路：
    暴露 M14 聚合接口：资源统计、Token 用量、趋势、模型占比，全部按 owner 隔离。

关键点：
    1. 全部依赖 get_current_user 取得 owner_id，实现数据隔离。
    2. 路径对齐前端 api/dashboard.js：/stats /token-usage /token-trend /model-stats。

实现逻辑：
    委托 dashboard_service 聚合；统一 success 包装，对齐 {code,msg,data} 契约。
"""
from fastapi import APIRouter, Depends, Query

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success
from services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats")
async def stats(user: User = Depends(get_current_user), session=Depends(get_session)):
    """资源统计卡片（M14.6）：小说/知识库/切片/实体/向量数。"""
    return success(await dashboard_service.get_stats(session, user.id))


@router.get("/token-usage")
async def token_usage(user: User = Depends(get_current_user), session=Depends(get_session)):
    """Token 消费总览（M14.3）：累计输入/输出、调用次数、费用。"""
    return success(await dashboard_service.get_token_usage(session, user.id))


@router.get("/token-trend")
async def token_trend(
    range: str = Query("day", description="day=按日(近14天), week=按周(近8周)"),
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """消费趋势图表（M14.4）：按日/周聚合 token 总量。"""
    if range not in ("day", "week"):
        range = "day"
    return success(await dashboard_service.get_token_trend(session, user.id, range))


@router.get("/model-stats")
async def model_stats(user: User = Depends(get_current_user), session=Depends(get_session)):
    """模型维度统计（M14.5）：各模型调用次数与费用。"""
    return success(await dashboard_service.get_model_stats(session, user.id))


@router.get("/tasks")
async def tasks(
    user: User = Depends(get_current_user),
    session=Depends(get_session),
    status: str | None = Query(None, description="running/success/failed/cancelled"),
    type: str | None = Query(None, description="parse/chunk/graph/character"),
    novel_name: str | None = Query(None, description="小说名模糊查询"),
    completed: bool | None = Query(None, description="是否完成：true=已结束, false=进行中"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    """异步任务进度总览（M14.1）：分页 + 条件查询（是否完成/小说名），任务列表 + 状态计数。"""
    data = await dashboard_service.get_task_overview(
        session, user.id, status=status, type=type, novel_name=novel_name,
        completed=completed, page=page, page_size=page_size,
    )
    return success(data)
