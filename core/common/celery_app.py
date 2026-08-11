"""
Celery 应用定义（混合分析管道 L2）

整体思路：
    定义全局 Celery app，broker/backend 均使用本机 Redis（端口 6363）。
    Celery worker 通过 `celery -A common.celery_app.celery_app worker` 启动，
    消费 common.celery_tasks 中注册的分析任务。本项目所有后台分析任务
    （解析/切章/人物/章节/图谱）最终都经此 app 分发到多 worker 并行执行。

关键点：
    1. broker_url / result_backend 指向 config.REDIS_URL（含端口 6363）。
    2. 老 Redis 3.2 只支持 broker 基础功能，故禁用心跳（broker_heartbeat=0）
       与可见性超时过短导致任务被误判为丢失：visibility_timeout 调大到 1 天，
       因为大模型分析任务单条可能耗时数十分钟（章节/图谱深度分析）。
    3. task_acks_late=True + 默认 prefetch=worker_concurrency：任务跑完才 ack，
       进程崩溃可由其他 worker 重新认领，保证全流程不丢任务。
    4. 任务结果不直接依赖 Celery backend（本项目用 task_service 落库进度），
       backend 仅作可选轮询兜底，可关闭以省 Redis 内存。

实现逻辑：
    模块加载即构建 Celery app；import 本项目任务模块完成注册（见 celery_tasks）。
"""
from celery import Celery

from config import REDIS_URL, REDIS_PASSWORD, CELERY_WORKER_CONCURRENCY

# 兼容约束：本机 Redis 为 3.2.100 老版本，redis-py 8.x 默认 RESP 协议为 3，
# 老节点不识别 HELLO 命令会报 "unknown command 'HELLO'"。
# celery/kombu 的 broker 连接未显式传 protocol，这里统一把本进程
# redis 连接的默认协议版本降为 2（与 common/redis_client.py 的显式设置一致）。
try:
    import redis
    redis.connection.DEFAULT_RESP_VERSION = 2
except Exception:  # noqa: BLE001
    pass

# 构建 Celery app
celery_app = Celery(
    "story_rag_pipeline",
    broker=REDIS_URL,
    backend=None,  # 进度由 task_service 落库，无需 Celery 结果后端（省内存）
    include=["common.celery_tasks"],
)

# Broker 连接参数：老 Redis 3.2 不支持 heartbeat / 某些扩展命令，稳妥起见关闭。
_broker_opts = {
    "broker_connection_retry": True,
    "broker_connection_retry_on_startup": True,
    "broker_heartbeat": 0,  # 老 Redis 无心跳支持
    "broker_connection_max_retries": 10,
}
if REDIS_PASSWORD:
    _broker_opts["broker_password"] = REDIS_PASSWORD

celery_app.conf.update(
    **_broker_opts,
    # 任务级可见性超时：大模型分析单任务可能数十分钟，必须远大于单任务耗时
    result_backend=None,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # 任务执行完后才 ack，配合 prefetch=1 让崩溃任务可被其他 worker 重领
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # 同一时刻本 worker 并发执行数（与启动参数 -c 一致，便于统一调优）
    worker_concurrency=CELERY_WORKER_CONCURRENCY,
    # 老旧 Redis 下任务可能长时间排队，放宽超时避免误判丢失
    broker_transport_options={
        "visibility_timeout": 86400,  # 24h
        "fanout_prefix": False,
        "fanout_patterns": False,
    },
    # 任务执行超时保护（单任务异常卡死则 kill 重投）
    task_time_limit=1800,    # 硬上限 30min
    task_soft_time_limit=1500,
)
