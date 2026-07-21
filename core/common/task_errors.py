"""
异步任务用户友好错误消息映射

整体思路：
    将系统异常（str(e)）转换为用户可理解的中文提示，避免在仪表盘/前端展示
    traceback 或技术性报错信息（如 "ConnectionRefusedError"）。

关键点：
    1. to_user_error(exception) 返回用户友好的中文消息。
    2. 支持 BizError 直接透传 msg（业务层已明确）。
    3. 常见网络/连接错误转为统一引导语（引导用户检查模型配置）。
    4. 未匹配的错误保留关键信息，但去掉技术细节（如完整 traceback）。

实现逻辑：
    按异常类型/关键字匹配 → 返回中文消息；兜底截断至 200 字并加前缀。
"""

from common.exceptions import BizError


# 关键字 → 用户友好消息映射（按优先级排序）
_ERROR_MAP = [
    # 连接/网络类
    ("ConnectError", "连接模型服务失败，请检查模型地址是否可访问"),
    ("ConnectionError", "连接模型服务失败，请检查模型地址是否可访问"),
    ("ConnectionRefusedError", "模型服务拒绝连接，请确认模型服务已启动"),
    ("ConnectionResetError", "模型服务连接被重置，请稍后重试"),
    ("TimeoutError", "模型服务响应超时，请检查网络或更换模型"),
    ("Timeout", "模型服务响应超时，请检查网络或更换模型"),
    ("ReadTimeout", "模型服务响应超时（章节过多或单次内容过长），请稍后重试"),
    ("ReadError", "模型服务连接异常，请检查模型服务状态"),
    ("HTTP 4", "模型服务返回客户端错误，请检查 API 密钥和模型名称是否正确"),
    ("HTTP 5", "模型服务返回服务端错误，请稍后重试或联系模型提供方"),
    ("401", "模型 API 密钥无效或已过期，请更新密钥配置"),
    ("403", "模型 API 无权限访问，请检查密钥权限"),
    ("429", "模型 API 调用频率超限，请稍后重试或降低并发"),
    ("500", "模型服务内部错误，请稍后重试"),
    ("503", "模型服务暂时不可用，请稍后重试"),
    # 配置/模型类
    ("尚未配置", None),  # 透传原始消息
    ("知识库未关联", None),
    ("暂无章节", None),
    ("文件丢失", None),
    ("文档不存在", None),
    ("知识库不存在", None),
    ("小说不存在", None),
    ("同小说下已存在同名知识库", None),
    ("已存在同名", None),
    ("文件超过上限", None),
    ("不支持的格式", None),
    ("该文件已上传过", None),
    ("无效的 API 密钥", "模型 API 密钥无效或已过期，请更新密钥配置"),
    ("model not found", "模型名称不存在，请检查模型配置中的模型名称"),
    ("invalid model", "模型名称无效，请检查模型配置"),
    ("API key", "API 密钥配置有误，请检查密钥是否正确"),
    # 嵌入/向量类
    ("embed", "向量嵌入失败，请检查嵌入模型配置是否正确"),
    ("vector", "向量操作失败，请稍后重试"),
    ("chroma", "向量数据库操作失败，请稍后重试"),
    ("pgvector", "向量数据库操作失败，请稍后重试"),
    # 文件/IO 类
    ("No such file", "文件读取失败，原始文档可能已被删除，请重新上传"),
    ("Permission denied", "文件读取权限不足，请联系管理员"),
    ("Disk full", "磁盘空间不足，请清理后重试"),
    # 通用兜底
    ("database", "数据库操作失败，请稍后重试"),
    ("sqlalchemy", "数据库操作失败，请稍后重试"),
    ("json", "数据解析失败，请重试"),
    ("memory", "系统内存不足，请稍后重试"),
]


def to_user_error(exception: Exception) -> str:
    """将异常转换为用户友好的中文错误提示。

    整体思路：
        优先匹配 BizError（业务层已明确），再按关键字匹配内置映射表，兜底做技术信息脱敏。
    关键点：
        1. BizError.msg 直接透传（业务层已做用户友好处理）。
        2. 关键字匹配不区分大小写。
        3. 兜底截断到 200 字并加「系统错误：」前缀。
    实现逻辑：
        取 str(e) → BizError 检查 → 关键字匹配 → 兜底截断。
    """
    msg = str(exception)

    # 1) BizError 直接透传业务消息
    if isinstance(exception, BizError):
        return exception.msg

    # 2) 关键字匹配（按优先级，首次命中即返回）
    msg_lower = msg.lower()
    for keyword, friendly in _ERROR_MAP:
        if keyword.lower() in msg_lower:
            if friendly is not None:
                return friendly
            else:
                # None 表示透传原始消息（如 "尚未配置对话模型"）
                return msg

    # 3) 兜底：去技术细节
    # 去掉换行和多余空格
    clean = " ".join(msg.replace("\n", " ").replace("\r", " ").split())
    # 截断到 200 字
    if len(clean) > 200:
        clean = clean[:197] + "..."
    return f"系统错误：{clean}" if clean else "系统错误：未知错误，请稍后重试"
