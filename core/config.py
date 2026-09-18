"""
配置模块

整体思路：
    从同目录 config.yml 读取运行配置，并允许环境变量覆盖，集中管理数据库、JWT、上传、CORS 等参数。

关键点：
    1. 配置源优先级：环境变量 > config.yml > 内置默认值。
    2. 对外暴露的常量名（DB_DSN 等）保持不变，调用方无需改动。
    3. 上传目录相对路径解析为基于本文件所在目录的绝对路径。

实现逻辑：
    模块加载时用 yaml.safe_load 解析 config.yml；逐项取环境变量或 yml 值；导出同名常量供其他模块 import。
"""
import base64
import hashlib
import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# 配置目录与 yml 路径（与 config.py 同目录）
_CONFIG_DIR = Path(__file__).resolve().parent
_CONFIG_PATH = _CONFIG_DIR / "config.yml"

# 读取 yml（缺失则用空字典，全部回退默认值）
with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
    _cfg = yaml.safe_load(_f) or {}

_database = _cfg.get("database", {})
_jwt = _cfg.get("jwt", {})
_upload = _cfg.get("upload", {})
_cors = _cfg.get("cors", {})
_encryption = _cfg.get("encryption", {})

# 数据库（异步 DSN）
DB_DSN = os.getenv("DB_DSN", _database.get("dsn", "postgresql+asyncpg://postgres:root@localhost:5432/story_rag"))

# 数据库（同步 DSN）：供 LangGraph 的 AsyncPostgresSaver（基于 psycopg）使用。
# psycopg 不认 SQLAlchemy 的 +asyncpg 方言前缀，故需剥离为纯 postgresql://。
# 若显式配置 LANGGRAPH_DB_DSN 则优先使用（便于 checkpoint 独立库/只读账号等场景）。
LANGGRAPH_DB_DSN = os.getenv(
    "LANGGRAPH_DB_DSN",
    DB_DSN.replace("postgresql+asyncpg://", "postgresql://")
          .replace("postgresql+psycopg://", "postgresql://"),
)

# JWT 鉴权
JWT_SECRET = os.getenv("JWT_SECRET", _jwt.get("secret", "story-rag-dev-secret-change-me"))
# P2-10：JWT 默认密钥仅用于本地开发；生产必须设置环境变量 JWT_SECRET 强密钥
if JWT_SECRET == "story-rag-dev-secret-change-me":
    logger.warning("JWT_SECRET 仍使用默认开发密钥，生产环境请通过环境变量 JWT_SECRET 设置强密钥！")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", _jwt.get("algorithm", "HS256"))
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", _jwt.get("expire_minutes", 60)))
# 访问令牌有效期上限（天）：默认 7 天，避免频繁登录；
# 续期接口可基于未过期令牌换新令牌，延长会话。
JWT_EXPIRE_DAYS = int(os.getenv("JWT_EXPIRE_DAYS", _jwt.get("expire_days", 7)))

# 上传限制（对齐 API 列表）
UPLOAD_DIR = os.getenv("UPLOAD_DIR", str(_CONFIG_DIR / _upload.get("dir", "uploads")))
MAX_TXT_BYTES = int(os.getenv("MAX_TXT_BYTES", _upload.get("max_txt_bytes", 50 * 1024 * 1024)))
MAX_OTHER_BYTES = int(os.getenv("MAX_OTHER_BYTES", _upload.get("max_other_bytes", 200 * 1024 * 1024)))
MAX_CHAPTERS = int(os.getenv("MAX_CHAPTERS", _upload.get("max_chapters", 5000)))
ALLOWED_EXT = set(_upload.get("allowed_ext", [".txt", ".epub", ".pdf", ".docx"]))

# 敏感信息加密密钥（Fernet，M9 模型 api_key / 后续 M11 MCP 凭据加密复用）
#   优先级：环境变量 ENCRYPTION_KEY > config.yml encryption.key > 由 JWT_SECRET 派生。
#   规则：44 字符视为合法 Fernet key 直接使用；否则按其内容 sha256 派生 32 字节 base64 key。
def _build_fernet_key(secret: str) -> bytes:
    """构建合法的 Fernet 密钥（32 字节 base64 编码）。"""
    if len(secret) == 44:
        return secret.encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())


_ENC_SECRET = os.getenv("ENCRYPTION_KEY", _encryption.get("key", "")) or JWT_SECRET
ENCRYPTION_KEY = _build_fernet_key(_ENC_SECRET)

# CORS（开发期放开，生产请收紧；支持环境变量 CORS_ORIGINS 覆盖，逗号分隔）
_CORS_ENV = os.getenv("CORS_ORIGINS")
if _CORS_ENV:
    CORS_ORIGINS = [o.strip() for o in _CORS_ENV.split(",") if o.strip()]
else:
    _CORS_ORIGINS = _cors.get("origins", ["*"])
    if isinstance(_CORS_ORIGINS, list):
        CORS_ORIGINS = _CORS_ORIGINS
    else:
        CORS_ORIGINS = str(_CORS_ORIGINS).split(",")
# P2-11：生产环境应通过环境变量 CORS_ORIGINS 收紧来源，避免 "*"
if "*" in CORS_ORIGINS:
    logger.warning("CORS 允许全部来源（*），生产环境请通过环境变量 CORS_ORIGINS 指定可信域名！")

# 后台异步任务线程池配置
# 关键点：CPU 密集型任务（切章）与慢速 LLM 调用放到独立线程执行，
#         避免阻塞主 asyncio 事件循环导致用户请求卡顿。
_bg = _cfg.get("background", {})
BG_THREAD_POOL_SIZE = int(os.getenv("BG_THREAD_POOL_SIZE", _bg.get("thread_pool_size", 8)))
BG_TASK_TIMEOUT = int(os.getenv("BG_TASK_TIMEOUT", _bg.get("task_timeout", 300)))

# 切片嵌入/落库并发与批大小（Chroma-only 流式分批，控制内存与远端压力）
#   embed_batch：单次嵌入请求携带的文本条数（OpenAI/Ollama 批量上限参考）
#   embed_concurrency：并发嵌入批次上限（信号量），避免打爆本地 Ollama/远端 API
#   chroma_upsert_batch：每多少条切片执行一次 bulk_insert + Chroma upsert（分批释放内存）
EMBED_BATCH = int(os.getenv("EMBED_BATCH", _bg.get("embed_batch", 32)))
EMBED_CONCURRENCY = int(os.getenv("EMBED_CONCURRENCY", _bg.get("embed_concurrency", 8)))
CHROMA_UPSERT_BATCH = int(os.getenv("CHROMA_UPSERT_BATCH", _bg.get("chroma_upsert_batch", 1000)))

# 向量库（chroma 本地持久化；后续可切 milvus / pgvector）
_vector_store = _cfg.get("vector_store", {})
VECTOR_STORE_TYPE = os.getenv("VECTOR_STORE_TYPE", _vector_store.get("type", "chroma"))
_chroma_dir = _vector_store.get("chroma", {}).get("persist_dir", "storage/chroma")
# 相对路径解析为基于本文件所在目录的绝对路径
VECTOR_STORE_CHROMA_DIR = os.getenv("VECTOR_STORE_CHROMA_DIR", str(_CONFIG_DIR / _chroma_dir))

# 存储后端（local / minio）
_storage = _cfg.get("storage", {})
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", _storage.get("backend", "local"))

# MinIO 对象存储
_minio = _cfg.get("minio", {})
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", _minio.get("endpoint", "127.0.0.1:9000"))
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", _minio.get("access_key", "minioadmin"))
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", _minio.get("secret_key", "minioadmin"))
MINIO_BUCKET = os.getenv("MINIO_BUCKET", _minio.get("bucket", "story-rag"))
MINIO_SECURE = str(os.getenv("MINIO_SECURE", _minio.get("secure", "false"))).lower() == "true"

# LangChain 调用层总开关（混合分析管道 M1）：所有大模型调用已统一收敛到 LangChain
# （langchain_factory.LangChainAdapter），旧 llm_adapters 已删除，本开关保留仅作环境兼容，
# 不再切换调用实现。默认 true。
# 配置源优先级：环境变量 USE_LANGCHAIN > config.yml langchain.enabled > 默认 true
USE_LANGCHAIN = (
    str(os.getenv("USE_LANGCHAIN", _cfg.get("langchain", {}).get("enabled", "true")))
    .lower() not in ("0", "false", "no", "off")
)

# 结果缓存开关（混合分析管道 M5 P3）：默认关，真实集成验证时置 true 启用进程内 TTL 缓存
#   优先级：环境变量 ENABLE_LLM_CACHE > config.yml cache.enabled > 默认 false
ENABLE_LLM_CACHE = (
    str(os.getenv("ENABLE_LLM_CACHE", _cfg.get("cache", {}).get("enabled", "false")))
    .lower() == "true"
)

# LLM 结果缓存 TTL（秒）：默认 3600，可由 config.yml cache.ttl 或环境变量 CACHE_TTL 覆盖。
# 仅在 ENABLE_LLM_CACHE=true 时生效（多 worker 下走 Redis 共享缓存）。
_CACHE = _cfg.get("cache", {})
CACHE_TTL = int(os.getenv("CACHE_TTL", _CACHE.get("ttl", 3600)))

# 注（P2.5，2026-09-18）：原 LANGGRAPH_ENABLED 开关已移除。
# 移除原因：线性冗余实现已删除，候选精析统一走 LangGraph 图（含质检 + 条件边重试），
# 开关失去意义且会造成「两条路径行为不一致」的运维困惑。
# 相关调优改用：GRAPH_LLM_CONCURRENCY（图内并行度）、CHARACTER_GRAPH_TIMEOUT（图超时）。

# ───────────────────────────────────────────────────────────────────────────
# L1 数据库连接池治理（混合分析管道性能优化）
#   全流程解析/切章/人物/章节/图谱分析并发会话较多，默认池太小(5)会耗尽连接。
#   默认 pool_size=20 显著大于 asyncpg 的 5，并开启 pre_ping 规避 PG 断连。
# ───────────────────────────────────────────────────────────────────────────
_database = _cfg.get("database", {})
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", _database.get("pool_size", 20)))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", _database.get("max_overflow", 10)))
DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", _database.get("pool_timeout", 30)))
DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", _database.get("pool_recycle", 1800)))
DB_POOL_PRE_PING = (
    str(os.getenv("DB_POOL_PRE_PING", _database.get("pool_pre_ping", "true"))).lower() == "true"
)

# ───────────────────────────────────────────────────────────────────────────
# L2 异步任务架构（Redis + Celery）—— 性能优化核心跃迁
#   现行 common.task_queue 是「单 worker 进程内串行 asyncio.Queue」，任何大模型
#   分析任务都只能排队逐一执行，全流程约 10h。L2 引入 Celery + Redis broker，
#   把任务分发到多 worker 并行执行，实现 8~12 worker 真并行。
#   关键约束：本机 Redis 为 3.2.100（老版本），redis-py 须用 protocol=2 兼容。
#   默认 USE_CELERY=false（opt-in），未装 celery 时回落到原串行队列，零破坏。
# ───────────────────────────────────────────────────────────────────────────
_redis = _cfg.get("redis", {})
REDIS_URL = os.getenv("REDIS_URL", _redis.get("url", "redis://127.0.0.1:6363/0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", _redis.get("password", "")) or None

_pipeline = _cfg.get("pipeline", {})
# 接通 Celery：默认 true 启用多 worker 并行（config.yml pipeline.async_mode），
# 使已启动的 celery worker 真正消费后台分析任务，不再回落单进程串行队列。
# 环境变量 USE_CELERY 仍可临时关闭（置 false）。
USE_CELERY = str(os.getenv("USE_CELERY", _pipeline.get("async_mode", "true"))).lower() == "true"
CELERY_WORKER_CONCURRENCY = int(os.getenv("CELERY_WORKER_CONCURRENCY", _pipeline.get("worker_concurrency", 8)))

# celery 可用性探测（导入期不强制依赖；未安装则 CELERY_AVAILABLE=False，回落串行）
try:
    import celery  # noqa: F401

    CELERY_AVAILABLE = True
except Exception:
    CELERY_AVAILABLE = False

# 在协程中运行同步/异步可调用体的辅助器（供 Celery worker 调用本项目 async 任务函数）
import asyncio


def run_async(coro_callable):
    """在线程/进程内执行一个协程可调用体，避免每个任务重复 asyncio.run 样板。

    实现逻辑：
        取当前运行时事件循环；若已存在且未关闭则直接执行，否则新建循环。
        Celery 的 prefork worker 每个进程有独立线程，首次调用时建循环即可复用。
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro_callable)


# ───────────────────────────────────────────────────────────────────────────
# 分析模式（极速 / 深度思考）—— 小说分析双模式入口
#   极速(turbo)：用 1~N 次大上下文调用产出「人物画像/情节概览/关系概览」散文摘要，
#               秒~分钟级，不建全量结构化库，与深度库并存（见 fast_analysis_service）。
#   深度(deep) ：走原有全量建库逻辑（人物档案/章节解析/图谱抽取），逐实体落结构化库。
#   默认极速：用户未显式选择时走极速，最快拿到可读摘要。
# ───────────────────────────────────────────────────────────────────────────
_analysis = _cfg.get("analysis", {})
ANALYSIS_MODE_DEFAULT = (
    str(os.getenv("ANALYSIS_MODE_DEFAULT", _analysis.get("mode_default", "turbo"))).lower()
)
# 极速模式单次投喂模型的最大正文字符数：模型上下文越大可上调（如 200000+），
# 越大则调用次数越少、越接近网页版「一次读完全书」体验。
TURBO_MAX_INPUT_CHARS = int(
    os.getenv("TURBO_MAX_INPUT_CHARS", _analysis.get("turbo_max_input_chars", 120000))
)
