"""
长耗时任务预估服务（V19 差异化展示）

整体思路：
    根据小说字数和各任务类型的处理速率，动态预估任务耗时；
    超过 10 分钟阈值标记为长任务，短任务保留在 Dashboard。

关键点：
    1. 处理速率按 mode 区分（turbo 远快于 deep），可配置动态校准。
    2. 预估公式：duration = ceil(word_count / chars_per_minute)，至少 1 分钟。
    3. is_long_task 判定阈值：THRESHOLD_MINUTES = 10。
    4. 预估不准时宁可偏长（高估让用户有心理预期），避免显示 "1分钟" 实际跑了 30 分钟。

实现逻辑：
    async def estimate_task(word_count, mode) → dict 返回 minutes、is_long、complete_at。
    速率在字典中配置，后续可从 DB 配置表读取实现动态校准。
"""
import math
from datetime import datetime, timedelta, timezone


# 长任务判定阈值（分钟）
THRESHOLD_MINUTES: int = 10

# 各分析模式的预估处理速率（字/分钟），可通过配置覆写
# turbo: 大上下文一次或几次调用就出结果，实际约 300~600 字/秒
# deep: 全链路 parse+chunk+embed+graph+character，约 20~60 字/秒
# parse: 纯解析入库不含向量化，约 200~400 字/秒
# chunk: chunk 切割 + embedding 入库，约 80~120 字/秒
CHAR_PER_MINUTE: dict[str, int] = {
    "turbo": 18000,     # 约 300 字/秒
    "deep": 2400,       # 约 40 字/秒
    "parse": 12000,     # 约 200 字/秒
    "chunk": 5000,      # 约 83 字/秒
    "default": 6000,    # 约 100 字/秒 兜底
}

# 强制标记为长任务的模式集合（deep、chunk 模式不管字数多少都走长任务中心）
FORCE_LONG_TASK_MODES: set[str] = {"deep", "chunk"}


def estimate_task_duration(word_count: int, mode: str = "default") -> dict:
    """
    根据小说字数和分析模式预估任务耗时。

    Args:
        word_count: 小说总字数（字符数）
        mode: 分析模式 — turbo / deep / parse / default

    Returns:
        dict: {
            estimated_minutes: int | None,    预估分钟数
            is_long_task: bool,               是否长任务（>THRESHOLD_MINUTES）
            estimated_complete_at: str | None, ISO 8601 预计完成时刻
        }
    """
    if word_count <= 0:
        # 字数为 0：未取到真实字数时，强制长任务模式给一个兜底预估（10 分钟）
        # 这样即便字数缺失，deep/chunk 等長任务也能正确出现在长任务中心。
        is_long = mode in FORCE_LONG_TASK_MODES
        est_min = THRESHOLD_MINUTES if is_long else None
        etc = (datetime.now(timezone.utc) + timedelta(minutes=est_min)) if est_min else None
        return {
            "estimated_minutes": est_min,
            "is_long_task": is_long,
            "estimated_complete_at": etc.isoformat() if etc else None,
        }

    cpm = CHAR_PER_MINUTE.get(mode, CHAR_PER_MINUTE["default"])
    estimated_minutes = max(1, math.ceil(word_count / cpm))
    # 深度模式统一走长任务中心，不受字数阈值影响
    is_long = estimated_minutes > THRESHOLD_MINUTES or mode in FORCE_LONG_TASK_MODES
    etc = None
    if estimated_minutes:
        etc = (datetime.now(timezone.utc) + timedelta(minutes=estimated_minutes)).isoformat()

    return {
        "estimated_minutes": estimated_minutes,
        "is_long_task": is_long,
        "estimated_complete_at": etc,
    }
