"""
Celery 任务注册与分发（混合分析管道 L2）

整体思路：
    把项目既有的异步分析函数（解析/切章/图谱/人物/章节/人物画像）注册为 Celery
    任务，由多 worker 并行消费。路由侧零改动：仍调用
    `task_queue.submit(func, *args, **kwargs)`，仅分发通道在 L2 下由「进程内串行
    队列」切换为「Celery + Redis broker」。

关键点：
    1. 通用分发任务 `dispatch_task(func_path, args, kwargs)`：broker 只认 JSON 可序列化
       内容，故函数以「module.qualname」路径字符串传递，运行时用 importlib 动态解析，
       无需手工维护注册表，且兼容私有函数（如 _run_parse）。
    2. 与串行队列完全等价：解析出 func 后执行 `func(*args, **kwargs)`（DB 任务 id
       已作为 args 首元素由路由注入，沿用既有约定）。
    3. 失败处理：捕获异常调用 task_service.fail_task 落库，复刻串行队列的错误处理，
       「运行失败」状态可回传前端。
    4. 复用 config.run_async 在 worker 进程内驱动 async 函数，避免到处 asyncio.run。

实现逻辑：
    导入 celery_app 完成注册 -> 定义 dispatch_task -> 动态解析函数 -> run_async 执行
    -> 异常落库失败（轻量重试一次以自愈瞬时限流）。
"""
import importlib
import traceback

from common.celery_app import celery_app
from config import run_async
from services import task_service


def _resolve_func(func_path: str):
    """按 'module.qualname' 动态解析出函数对象（兼容私有/嵌套函数）。"""
    module_name, _, qualname = func_path.rpartition(".")
    module = importlib.import_module(module_name)
    obj = module
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


async def _mark_failed(task_id, exc: Exception) -> None:
    """任务异常时落库失败状态（与串行队列行为一致）。"""
    try:
        await task_service.fail_task(task_id, str(exc))
    except Exception:  # noqa: BLE001
        pass


@celery_app.task(name="pipeline.dispatch", bind=True, max_retries=1)
def dispatch_task(self, func_path: str, args: list, kwargs: dict):
    """通用分发任务：解析函数 -> 执行 -> 异常落库失败。

    参数：
        func_path : "module.qualname" 形式，定位实际分析函数。
        args/kwargs: 透传给分析函数（DB 任务 id 已作为 args 首元素）。
    """
    try:
        func = _resolve_func(func_path)
    except Exception as exc:
        print(f"[celery] 无法解析函数 {func_path}: {exc}")
        return
    # args[0] 即 DB story_async_task.id
    task_id = args[0] if args else None
    try:
        run_async(func(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001
        print(f"[celery] 任务失败 func={func_path} task_id={task_id}: {exc}")
        traceback.print_exc()
        if task_id is not None:
            run_async(_mark_failed(task_id, exc))
        try:
            raise self.retry(exc=exc, countdown=10)
        except Exception:
            pass
