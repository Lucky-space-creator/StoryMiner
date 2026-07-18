"""
后台任务线程池工具

整体思路：
    提供一个进程级 ThreadPoolExecutor，用于把 CPU 密集型任务（如切章）与慢速
    同步调用（如 LLM 一次性 chat）从 asyncio 事件循环中剥离到独立线程执行，
    避免阻塞主事件循环导致用户 HTTP 请求卡顿。

关键点：
    1. 单例线程池，进程内复用，避免每次任务重复创建/销毁线程。
    2. 对外暴露 run_in_thread(func, *args, **kwargs)：把同步函数包装为可 await 的协程。
    3. 线程数取自 config.BG_THREAD_POOL_SIZE，默认 8（适配开发期并发）。
    4. 异常透传：线程内抛出的异常会通过 future.result() 重新抛出，调用方按需捕获。

实现逻辑：
    模块加载时构建 ThreadPoolExecutor；run_in_thread 内部用 asyncio.get_running_loop
    拿到当前事件循环，调 run_in_executor 把同步函数派发到线程池并 await 结果。
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Awaitable, Callable, TypeVar

from config import BG_THREAD_POOL_SIZE

T = TypeVar("T")

# 进程级单例线程池：所有后台 CPU/同步任务共用
_executor = ThreadPoolExecutor(
    max_workers=BG_THREAD_POOL_SIZE,
    thread_name_prefix="bg-task",
)


def run_in_thread(func: Callable[..., T], *args, **kwargs) -> Awaitable[T]:
    """把同步函数 func 派发到后台线程池执行，返回可 await 的协程。

    整体思路：
        取当前事件循环 → loop.run_in_executor(_executor, partial(func, *args, **kwargs))
    关键点：
        1. 不阻塞当前事件循环，主线程可继续响应其他 HTTP 请求。
        2. 线程内抛出的异常在 await 时重新抛出，由调用方按需捕获。
    实现逻辑：
        用 functools.partial 绑定参数，再交给 run_in_executor 调度。
    """
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(_executor, partial(func, *args, **kwargs))


def shutdown() -> None:
    """进程退出时优雅关闭线程池（注册到 atexit 或 lifespan shutdown）。"""
    _executor.shutdown(wait=False, cancel_futures=True)
