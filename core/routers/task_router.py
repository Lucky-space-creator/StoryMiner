"""
统一异步任务路由（/api/v1/tasks）

整体思路：
    暴露异步任务的列表、进行中列表、详情、SSE 实时流接口。
    SSE 流（/tasks/stream）替代前端对 /running、/long 的定时轮询，后端在进度回写时主动推送。

关键点：
    1. 全部依赖 get_current_user 取得 owner_id，实现数据隔离。
    2. /running 仅返回进行中任务（保留以兼容仪表盘等低频查询）。
    3. 详情校验归属，避免越权读取他人任务。
    4. /stream 必须定义在 /{task_id} 之前，否则 FastAPI 会把 stream 当作 task_id 匹配。

实现逻辑：
    委托 task_service；统一 success 包装，对齐 {code,msg,data} 契约。
"""
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from datetime import datetime, timezone

from models.user import User
from db import get_session, SessionLocal
from auth.jwt import get_current_user, get_current_user_minimal, decode_token
from common.response import success
from common.exceptions import BizError
from common import task_cancel
from common.task_pubsub import task_event_stream
from services import task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])

# 不同任务类型的取消原因文案（用户主动取消时回写 error）
_CANCEL_REASON = {
    "character_analysis": "用户主动取消人物",
    "character": "用户主动取消小传生成",
    "chapter_analysis": "用户主动取消章节解析",
}


@router.get("")
async def list_tasks(
    user: User = Depends(get_current_user),
    session=Depends(get_session),
    status: str | None = Query(None, description="running/success/failed/cancelled"),
    type: str | None = Query(None, description="parse/chunk/graph/character/chapter_analysis"),
    novel_name: str | None = Query(None, description="小说名模糊查询"),
    completed: bool | None = Query(None, description="是否完成：true=已结束, false=进行中"),
    is_long_task: bool | None = Query(None, description="V19 筛选长/短任务：true=长任务，false=短任务"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    """任务列表（分页 + 条件查询：状态/类型/是否完成/小说名/长短任务）。"""
    return success(await task_service.query_tasks(
        session, user.id, status=status, type=type,
        novel_name=novel_name, completed=completed, is_long_task=is_long_task,
        page=page, page_size=page_size,
    ))


@router.get("/running")
async def running(user: User = Depends(get_current_user), session=Depends(get_session)):
    """进行中任务列表（前端全局轮询）。"""
    return success(await task_service.list_running(session, user.id))


@router.get("/long")
async def long_tasks(
    user: User = Depends(get_current_user),
    session=Depends(get_session),
    status: str | None = Query(None, description="running/success/failed/cancelled，不传=全部"),
    type: str | None = Query(None, description="parse/chunk/graph/character"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    """V19 长任务中心：仅展示长任务（预估 >10分钟），显示预计完成时刻而非进度条。

    前端长任务中心页面（LongTaskCenter）调用此接口，按状态筛选、分页加载。
    """
    return success(await task_service.query_tasks(
        session, user.id, status=status, type=type, is_long_task=True,
        page=page, page_size=page_size,
    ))


async def _resolve_sse_user(request: Request, token: str | None = Query(None), session=Depends(get_session)) -> User:
    """SSE 鉴权：优先 Authorization 头，缺省时回退 ?token= 查询参数（EventSource 无法自定义请求头）。"""
    raw = request.headers.get("Authorization")
    uid = None
    if raw and raw.startswith("Bearer "):
        uid = decode_token(raw.split(" ", 1)[1])
    elif token:
        uid = decode_token(token)
    if uid is None:
        raise BizError(401, "未登录")
    user = await get_current_user_minimal(uid, session)
    if not user:
        raise BizError(401, "用户不存在")
    return user


@router.get("/stream")
async def task_stream(
    request: Request,
    user: User = Depends(_resolve_sse_user),
    session=Depends(get_session),
):
    """V20 统一任务实时流（SSE）：推送当前用户全部任务的进度/状态变更，替代前端定时轮询。

    前端通过 EventSource('/api/v1/tasks/stream?token=...') 连接；后端在每次进度回写时广播快照。
    事件类型：snapshot（全量）、task（单任务变更，含终态）。
    """
    async def _current_snapshot():
        # 返回该用户最近的任务快照（进行中 + 近期结束），供连接建立时初始推送。
        # 注意：此处必须用独立 SessionLocal()，不能复用路由注入的 session。
        # 原因：StreamingResponse 开始迭代生成器时，路由函数已返回，依赖注入的
        # session 已在 get_session 的 finally 中关闭；在其上执行查询会抛
        # AsyncOperationError / InvalidRequestError，进而触发 HTTP 500 并中断 SSE 流，
        # 前端 EventSource 因连接异常关闭而无限重连，造成界面卡死/数据消失的观感。
        async with SessionLocal() as snap_session:
            rows = await task_service.list_tasks(snap_session, user.id, limit=50)
        return rows

    return StreamingResponse(
        task_event_stream(user.id, _current_snapshot, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{task_id}")
async def detail(task_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """任务详情。"""
    t = await task_service.get_task(session, task_id)
    if not t or t.owner_id != user.id:
        raise BizError(404, "任务不存在")
    return success(task_service._out(t))


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """取消进行中的异步任务：登记取消标志并立即回写「已取消」状态，后台任务在循环边界感知后停止。

    关键点：
        1. 仅 running 状态可取消，已结束任务返回提示。
        2. 取消原因按任务类型映射（人物分析→用户主动取消人物）。
        3. 立即设置内存标志 + DB 状态，前端秒级可见；token 消耗由后台任务在停止前回写。
    """
    t = await task_service.get_task(session, task_id)
    if not t or t.owner_id != user.id:
        raise BizError(404, "任务不存在")
    if t.status != "running":
        raise BizError(400, "任务已结束，无法取消")
    reason = _CANCEL_REASON.get(t.type, "用户主动取消任务")
    task_cancel.request_cancel(task_id)
    await task_service.update_task_progress(
        task_id, stage="cancelled", status="cancelled", error=reason,
        finished_at=datetime.now(timezone.utc),
    )
    return success(None, "取消请求已提交")
