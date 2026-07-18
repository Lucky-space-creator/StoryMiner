"""
统一异步任务路由（/api/v1/tasks）

整体思路：
    暴露异步任务的列表、进行中列表、详情接口，供仪表盘总览与前端全局轮询使用。

关键点：
    1. 全部依赖 get_current_user 取得 owner_id，实现数据隔离。
    2. /running 仅返回进行中任务，供前端每 5s 轮询刷新进度。
    3. 详情校验归属，避免越权读取他人任务。

实现逻辑：
    委托 task_service；统一 success 包装，对齐 {code,msg,data} 契约。
"""
from fastapi import APIRouter, Depends, Query

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success
from common.exceptions import BizError
from services import task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(
    user: User = Depends(get_current_user),
    session=Depends(get_session),
    status: str | None = Query(None, description="running/success/failed"),
    type: str | None = Query(None, description="parse/chunk/graph/character"),
    limit: int = Query(50, ge=1, le=200),
):
    """任务列表（可按状态/类型过滤）。"""
    return success(await task_service.list_tasks(session, user.id, status, type, limit))


@router.get("/running")
async def running(user: User = Depends(get_current_user), session=Depends(get_session)):
    """进行中任务列表（前端全局轮询）。"""
    return success(await task_service.list_running(session, user.id))


@router.get("/{task_id}")
async def detail(task_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """任务详情。"""
    t = await task_service.get_task(session, task_id)
    if not t or t.owner_id != user.id:
        raise BizError(404, "任务不存在")
    return success(task_service._out(t))
