"""
全局串行任务队列（所有后台异步任务共享）

整体思路：
    用一个进程内 asyncio.Queue + 单个 worker 协程，把所有耗时后台任务（解析/切割/
    图谱/人物/章节解析等）改为「逐一排队、顺序执行」，同一时刻只跑一个，避免并发
    造成的 LLM 限流、资源争用与进度混乱。排队中的任务在 DB 中保持 stage="pending"，
    前端据此显示「排队中」。

关键点：
    1. 队列与 worker 必须在事件循环内创建/启动（延迟初始化）。
    2. submit 为同步入队（put_nowait），路由/服务无需 await，快速返回。
    3. worker 逐一取出任务执行，单个任务异常不影响后续任务（兜底 try/except）。
    4. 任务函数内部自行回写 DB 进度；本模块只负责调度顺序，不感知业务细节。

实现逻辑：
    start_worker 在应用启动时创建长驻 worker；submit 把 (func, args, kwargs) 放入队列；
    worker 循环 await queue.get() 后 await func(*args, **kwargs)。
"""
import asyncio
import logging

from config import USE_CELERY, CELERY_AVAILABLE

logger = logging.getLogger(__name__)

# 进程内单例队列与 worker，延迟到事件循环内初始化
_queue: "asyncio.Queue | None" = None
_worker_task: "asyncio.Task | None" = None


def _get_queue() -> asyncio.Queue:
    """获取（或惰性创建）全局任务队列。"""
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


def _func_path(func) -> str:
    """将函数对象转为 'module.qualname' 路径字符串，供 Celery broker 序列化传递。"""
    return f"{func.__module__}.{func.__qualname__}"


def submit(func, *args, **kwargs) -> None:
    """将一个 async 任务函数入队执行（同步入队，立即返回）。

    参数：
        func: 待执行的协程函数（async def）。DB 任务 id 已作为 args 首元素注入。
        *args/**kwargs: 传给 func 的参数。

    L2 双模说明：
        当 config.USE_CELERY=True 且 celery 已安装时，任务投递到 Celery + Redis broker，
        由多 worker 并行执行；若投递失败（如参数含不可 JSON 序列化的复杂对象），
        安全回落到原进程内串行队列，保证任何情况下任务都能跑、接口不 500。
    """
    if USE_CELERY and CELERY_AVAILABLE:
        try:
            from common.celery_tasks import dispatch_task

            dispatch_task.delay(_func_path(func), list(args), kwargs)
            logger.info("任务已投递 Celery: %s", _func_path(func))
            return
        except Exception as exc:  # 序列化失败/ broker 不可达 -> 回落串行
            logger.warning("Celery 投递失败，回落串行队列: %s (%s)", _func_path(func), exc)
    _get_queue().put_nowait((func, args, kwargs))
    logger.info("任务入队，当前队列积压 %d 个", _get_queue().qsize())


async def _worker() -> None:
    """常驻 worker：逐一取出任务并顺序执行，单任务异常不影响后续。

    V20 加固：单任务整体加硬超时（默认 30min），避免任意环节（LLM 挂起/死循环）
    永久阻塞唯一 worker 导致后续任务全部饿死在 pending。超时任务被取消并记日志，
    队列继续处理后续任务。
    """
    q = _get_queue()
    # 单任务最大执行时长：默认 30 分钟，可用环境变量 TASK_HARD_TIMEOUT 覆盖
    import os as _os
    _hard = float(_os.getenv("TASK_HARD_TIMEOUT", "1800"))
    logger.info("串行任务队列 worker 已启动（单任务硬超时 %.0fs）", _hard)
    while True:
        func, args, kwargs = await q.get()
        try:
            await asyncio.wait_for(func(*args, **kwargs), timeout=_hard)
        except asyncio.TimeoutError:
            logger.error("串行任务执行超时(>%.0fs)被取消: %s", _hard, _func_path(func))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("串行任务执行异常: %s", e, exc_info=True)
        finally:
            q.task_done()


def start_worker() -> "asyncio.Task":
    """启动常驻 worker（幂等：已在运行则复用）。应在应用启动时调用。"""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker())
    return _worker_task


async def stop_worker() -> None:
    """停止 worker（应用关闭时调用）。"""
    global _worker_task
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
