"""
业务异常

整体思路：
    统一业务错误，由 main 的异常处理器转换为 {code,msg,data:null}。

关键点：
    1. 携带 code 与 msg，直接映射到响应契约。

实现逻辑：
    继承 Exception，保存 code/msg。
"""

class BizError(Exception):
    """业务异常：携带错误码与提示信息。"""

    def __init__(self, code: int = 1, msg: str = "error"):
        self.code = code
        self.msg = msg
        super().__init__(msg)
