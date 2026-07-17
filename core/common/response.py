"""
统一响应包装

整体思路：
    按 API 契约返回 {code, msg, data}，分页统一 {list,total,page,size}。

关键点：
    1. success/error 返回纯 dict，由 FastAPI 直接序列化。
    2. paginate 封装列表分页结构。

实现逻辑：
    纯函数，无副作用。
"""


def success(data=None, msg: str = "ok"):
    """成功响应：code=0。"""
    return {"code": 0, "msg": msg, "data": data}


def error(code: int = 1, msg: str = "error", data=None):
    """失败响应：code!=0。"""
    return {"code": code, "msg": msg, "data": data}


def paginate(items, total: int, page: int, size: int):
    """分页结构：{list,total,page,size}。"""
    return {"list": items, "total": total, "page": page, "size": size}
