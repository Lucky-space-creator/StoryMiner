"""
解析任务路由（/api/v1/parse-tasks）

整体思路：
    暴露解析任务列表与 SSE 进度推送，独立于 /novels/{id} 以免路由冲突。

关键点：
    1. 列表与进度均按 owner 隔离（progress 校验归属）。
    2. 进度用 SSE（text/event-stream），事件结构对齐 API 契约。

实现逻辑：
    委托 parse_service；SSE 用 StreamingResponse 包装异步生成器。
"""
from fastapi import APIRouter, Depends, Request, Query, BackgroundTasks
from fastapi.responses import StreamingResponse

from models.user import User
from db import get_session
from auth.jwt import get_current_user, decode_token
from common.exceptions import BizError
from common.response import success
from services import parse_service

router = APIRouter(prefix="/parse-tasks", tags=["parse-tasks"])


async def resolve_sse_user(
    request: Request,
    token: str | None = Query(None),
    session=Depends(get_session),
) -> User:
    """SSE 鉴权：优先取 Authorization 头，缺省时回退到 ?token= 查询参数（EventSource 无法自定义请求头）。"""
    raw = request.headers.get("Authorization")
    if raw and raw.startswith("Bearer "):
        uid = decode_token(raw.split(" ", 1)[1])
    elif token:
        uid = decode_token(token)
    else:
        raise BizError(401, "未登录或缺少令牌")
    user = await session.get(User, uid)
    if not user:
        raise BizError(401, "用户不存在")
    return user


@router.get("")
async def parse_tasks(user: User = Depends(get_current_user), session=Depends(get_session)):
    """当前用户解析任务列表。"""
    return success(await parse_service.list_tasks(session, user.id))


@router.get("/{task_id}/progress")
async def parse_progress(task_id: int, user: User = Depends(resolve_sse_user), session=Depends(get_session)):
    """SSE 推送解析进度（M1.7）。"""
    return StreamingResponse(
        parse_service.progress_stream(session, task_id, user.id),
        media_type="text/event-stream",
    )


@router.post("/{task_id}/retry")
async def retry(task_id: int, background: BackgroundTasks, user: User = Depends(get_current_user), session=Depends(get_session)):
    """失败任务重试（M14.2）：重置状态并重新调度后台解析。"""
    return success(await parse_service.retry_task(session, user.id, task_id, background))
