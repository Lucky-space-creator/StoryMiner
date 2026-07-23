"""
FastAPI 应用入口

整体思路：
    组装应用：CORS、异常处理器、挂载 /api/v1 路由（auth + novels + ... + skills）。

关键点：
    1. 统一响应契约 {code,msg,data}，业务异常转 200 + {code!=0}。
    2. 路由前缀 /api/v1，鉴权除 /auth 外均依赖 get_current_user。
    3. 启动钩子自动建表并 seed 内置 Skill 库（M10.2，幂等）。

实现逻辑：
    创建 FastAPI；注册 CORS 中间件；注册 BizError/校验异常处理器；include_router；
    lifespan 内 create_all + seed_builtin。
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager

from config import CORS_ORIGINS
from db import engine, SessionLocal
from models.base import Base
from common.response import error
from common.exceptions import BizError
from routers import auth as auth_router
from routers import novels as novels_router
from routers import parse_tasks as parse_tasks_router
from routers import knowledge_bases as kb_router
from routers import documents as documents_router
from routers import llm_configs as llm_configs_router
from routers import prompt_templates as prompt_templates_router
from routers import chunks as chunks_router
from routers import graph as graph_router
from routers import characters as characters_router
from routers import writing as writing_router
from routers import ws_write as ws_write_router
from routers import ws_reading_chat as ws_reading_chat_router
from routers import reading_chat as reading_chat_router
from routers import skills as skills_router
from routers import extension as extension_router
from routers import dashboard as dashboard_router
from routers import task_router as task_router
from services import skill_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动钩子：自动建表 + seed 内置 Skill 库（幂等），便于 M8/M10 等新表零手动落地。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 兼容：确保内置 Skill 的 owner_id 可空（修正早期非空列），失败忽略
        try:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE story_skill ALTER COLUMN owner_id DROP NOT NULL"))
        except Exception:
            pass
        # 方案A：文档与知识库解耦，kb_id 改为可空（create_all 不会改列约束，需显式 ALTER）
        try:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE story_document ALTER COLUMN kb_id DROP NOT NULL"))
        except Exception:
            pass
        # 迁移历史数据：原 kb_id 非空的文档建立关联（幂等，重复执行忽略）
        try:
            from sqlalchemy import text
            await conn.execute(text(
                "INSERT INTO story_kb_document (kb_id, doc_id, owner_id, created_at) "
                "SELECT kb_id, id, owner_id, created_at FROM story_document "
                "WHERE kb_id IS NOT NULL AND deleted_at IS NULL "
                "ON CONFLICT (kb_id, doc_id) DO NOTHING"
            ))
        except Exception:
            pass
        # V10：AI 概括字段（story_novel.ai_summary）。create_all 不会给已存在的表加列，
        # 模型已使用该字段，缺失会致 /api/v1/novels 等接口 500，故显式 ALTER 补齐（幂等）。
        try:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE story_novel ADD COLUMN IF NOT EXISTS ai_summary TEXT"))
        except Exception:
            pass
        # V12：异步任务取消 + Token 消耗记录（story_async_task.tokens_in/tokens_out）。
        # create_all 不会给已存在的表加列，显式 ALTER 补齐（幂等）。
        try:
            from sqlalchemy import text
            await conn.execute(text(
                "ALTER TABLE story_async_task ADD COLUMN IF NOT EXISTS tokens_in INTEGER NOT NULL DEFAULT 0"
            ))
            await conn.execute(text(
                "ALTER TABLE story_async_task ADD COLUMN IF NOT EXISTS tokens_out INTEGER NOT NULL DEFAULT 0"
            ))
        except Exception:
            pass
        # V16：阅读辅助对话扩展字段（story_conversation / story_conversation_message）。
        # create_all 不会给已存在的表加列，显式 ALTER 补齐（幂等）。
        try:
            from sqlalchemy import text
            await conn.execute(text(
                "ALTER TABLE story_conversation "
                "ADD COLUMN IF NOT EXISTS context_window integer NOT NULL DEFAULT 4000, "
                "ADD COLUMN IF NOT EXISTS keep_recent integer NOT NULL DEFAULT 10, "
                "ADD COLUMN IF NOT EXISTS compressed_summary text"
            ))
            await conn.execute(text(
                "ALTER TABLE story_conversation_message "
                "ADD COLUMN IF NOT EXISTS attachments jsonb NOT NULL DEFAULT '[]'::jsonb, "
                "ADD COLUMN IF NOT EXISTS is_compressed boolean NOT NULL DEFAULT false, "
                "ADD COLUMN IF NOT EXISTS tokens integer NOT NULL DEFAULT 0"
            ))
        except Exception:
            pass
        # 清理上次进程遗留的僵尸任务：内存队列随重启清空，DB 中残留的 running/pending
        # 任务不会再被执行，标记为 failed，避免前端一直显示「进行中/排队中」。
        try:
            from sqlalchemy import text
            await conn.execute(text(
                "UPDATE story_async_task SET status='failed', error='服务重启，任务已中断' "
                "WHERE status='running'"
            ))
        except Exception:
            pass
    async with SessionLocal() as s:
        await skill_service.seed_builtin(s)
    # 确保对象存储桶存在（MinIO 后端；失败仅告警不阻断启动）
    try:
        from storage import ensure_storage
        await ensure_storage()
    except Exception as e:  # noqa: BLE001
        print("[warn] 存储初始化失败:", e)
    # 启动全局串行任务队列 worker：所有后台任务逐一排队执行
    from common import task_queue
    task_queue.start_worker()
    yield
    # 进程退出时优雅关闭：停止串行队列 worker + 后台线程池
    try:
        await task_queue.stop_worker()
    except Exception:
        pass
    try:
        from common.threadpool import shutdown
        shutdown()
    except Exception:
        pass


app = FastAPI(title="小说解析 RAG 系统", version="v1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(BizError)
async def biz_error_handler(request: Request, exc: BizError):
    """业务异常 -> {code,msg,data:null}（HTTP 200，对齐契约）。"""
    return JSONResponse(status_code=200, content=error(exc.code, exc.msg))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """参数校验失败 -> {code:422,msg,data:errors}。"""
    return JSONResponse(status_code=200, content=error(422, "参数校验失败", exc.errors()))


app.include_router(auth_router.router, prefix="/api/v1")
app.include_router(novels_router.router, prefix="/api/v1")
app.include_router(parse_tasks_router.router, prefix="/api/v1")
app.include_router(kb_router.router, prefix="/api/v1")
app.include_router(documents_router.router, prefix="/api/v1")
app.include_router(llm_configs_router.router, prefix="/api/v1")
app.include_router(prompt_templates_router.router, prefix="/api/v1")
app.include_router(chunks_router.router, prefix="/api/v1")
app.include_router(graph_router.router, prefix="/api/v1")
app.include_router(graph_router.rt_router, prefix="/api/v1")
app.include_router(graph_router.et_router, prefix="/api/v1")
app.include_router(characters_router.novel_router, prefix="/api/v1")
app.include_router(characters_router.char_router, prefix="/api/v1")
app.include_router(writing_router.router, prefix="/api/v1")
app.include_router(ws_write_router.ws_router)
app.include_router(ws_reading_chat_router.ws_router)
app.include_router(reading_chat_router.router, prefix="/api/v1")
app.include_router(skills_router.router, prefix="/api/v1")
app.include_router(extension_router.router, prefix="/api/v1")
app.include_router(dashboard_router.router, prefix="/api/v1")
app.include_router(task_router.router, prefix="/api/v1")


@app.get("/")
async def root():
    """健康检查。"""
    return error(0, "ok", {"service": "story-rag", "docs": "/docs"})
