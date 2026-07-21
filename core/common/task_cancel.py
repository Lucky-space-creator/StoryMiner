"""
异步任务取消标志（进程内）

整体思路：
    提供轻量级进程内取消标志，供后台协程在循环边界检测用户主动取消请求。
    单进程后端下，取消请求与后台任务在同一进程，内存集合即可满足。

关键点：
    1. request_cancel(task_id) 登记取消；is_cancelled(task_id) 供后台任务轮询。
    2. TaskCancelled 为自定义异常，后台任务检测到取消时抛出，由 service 捕获后
       回写「已取消」状态并记录已消耗的 token。
    3. 取消标志为幂等，任务进入终态后由调用方 clear 清理（避免内存堆积）。

实现逻辑：
    以 set 维护已请求取消的 task_id；异常类携带原因与已消耗 token 供回写。
"""
_flags: set[int] = set()


def request_cancel(task_id: int) -> None:
    """登记取消请求（幂等）。"""
    _flags.add(task_id)


def is_cancelled(task_id: int) -> bool:
    """任务是否已被请求取消。"""
    return task_id in _flags


def clear(task_id: int) -> None:
    """清理取消标志（任务进入终态后调用）。"""
    _flags.discard(task_id)


class TaskCancelled(Exception):
    """后台任务被用户主动取消：携带已消耗 token 供回写。"""

    def __init__(self, reason: str = "用户主动取消任务", tokens_in: int = 0, tokens_out: int = 0):
        self.reason = reason
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        super().__init__(reason)
