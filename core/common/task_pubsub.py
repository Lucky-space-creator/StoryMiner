"""
统一任务进度广播（SSE 实时推送骨架）

整体思路：
    替代前端对 /tasks/running、/tasks/long 的定时轮询。后端在每次任务进度回写
    （task_service.update_task_progress）提交后，向该 owner 的所有 SSE 订阅者推送任务快照。
    订阅键为 owner_id（一用户一连接，推送其全部任务），而非单 task_id，避免多任务多连接。

关键点：
    1. 纯内存发布订阅，依赖 asyncio.Queue；进程重启后订阅清空（前端自动重连重建连接）。
    2. 仅按 owner 分组，不感知具体 task，所有任务类型共用一条通道。
    3. heartbeat 保活：SSE 连接空闲时周期性发送注释帧，避免代理（nginx）断连。
    4. 广播失败（如某订阅者已断开）仅记日志，不影响主业务回写。

实现逻辑：
    subscribe(owner_id) 注册队列并返回；
    publish(owner_id, snapshot) 向该 owner 全部队列 put 快照；
    生成器 task_event_stream 负责「先推当前全量任务→订阅→循环收事件/心跳」。
"""
import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)

# owner_id -> [asyncio.Queue, ...]，内存订阅表
_subscribers: dict[int, list[asyncio.Queue]] = {}

# 心跳间隔（秒）：空闲时发送 SSE 注释帧保活
_HEARTBEAT_INTERVAL: float = 15.0


def subscribe(owner_id: int) -> asyncio.Queue:
    """注册一个 SSE 订阅队列，返回供生成器消费的 Queue。"""
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(owner_id, []).append(q)
    return q


def _remove(owner_id: int, q: asyncio.Queue) -> None:
    """连接关闭时从订阅表移除队列。"""
    subs = _subscribers.get(owner_id)
    if subs and q in subs:
        subs.remove(q)
        if not subs:
            _subscribers.pop(owner_id, None)


async def publish(owner_id: int, snapshot: dict) -> None:
    """向某 owner 的全部 SSE 订阅者推送任务快照（非阻塞，满队列直接丢弃）。"""
    subs = _subscribers.get(owner_id)
    if not subs:
        return
    dead = []
    for q in subs:
        try:
            q.put_nowait(snapshot)
        except asyncio.QueueFull:
            # 订阅者消费过慢，丢弃本次快照（SSE 允许丢帧，下次事件仍会刷新）
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("任务广播失败 owner=%s: %s", owner_id, e)
            dead.append(q)
    for q in dead:
        _remove(owner_id, q)


def _sse_frame(event: dict) -> str:
    """将事件序列化为标准 SSE data 帧。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def task_event_stream(owner_id: int, current_snapshot_fn, request):
    """SSE 异步生成器：先推当前全量任务快照，再持续推送广播事件与心跳。

    Args:
        owner_id: 订阅用户 id
        current_snapshot_fn: 无参 async 函数，返回当前该用户全部任务快照列表（dict 列表）
        request: Starlette Request 对象，用 await request.is_disconnected() 感知连接断开
    """
    q = subscribe(owner_id)
    try:
        # 建立连接后立即推送一次当前全量状态（避免前端空等）
        try:
            initial = await current_snapshot_fn()
            yield _sse_frame({"type": "snapshot", "tasks": initial})
        except Exception as e:  # noqa: BLE001
            logger.warning("初始任务快照推送失败 owner=%s: %s", owner_id, e)

        last_heartbeat = time.monotonic()
        while True:
            # 连接断开则退出（request.is_disconnected 是协程，需 await）
            try:
                if await request.is_disconnected():
                    break
            except Exception:  # noqa: BLE001
                break
            # 优先消费广播事件（非阻塞），否则等待 1s 或心跳到期
            try:
                event = await asyncio.wait_for(q.get(), timeout=1.0)
                yield _sse_frame({"type": "task", "task": event})
            except asyncio.TimeoutError:
                pass
            # 心跳保活
            if time.monotonic() - last_heartbeat >= _HEARTBEAT_INTERVAL:
                yield ": heartbeat\n\n"
                last_heartbeat = time.monotonic()
    finally:
        _remove(owner_id, q)
