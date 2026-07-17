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
from routers import conversations as conversations_router
from routers import ws_chat as ws_chat_router
from routers import writing as writing_router
from routers import ws_write as ws_write_router
from routers import skills as skills_router
from routers import extension as extension_router
from routers import dashboard as dashboard_router
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
    async with SessionLocal() as s:
        await skill_service.seed_builtin(s)
    # 确保对象存储桶存在（MinIO 后端；失败仅告警不阻断启动）
    try:
        from storage import ensure_storage
        await ensure_storage()
    except Exception as e:  # noqa: BLE001
        print("[warn] 存储初始化失败:", e)
    yield


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
app.include_router(characters_router.novel_router, prefix="/api/v1")
app.include_router(characters_router.char_router, prefix="/api/v1")
app.include_router(conversations_router.router, prefix="/api/v1")
app.include_router(ws_chat_router.ws_router)
app.include_router(writing_router.router, prefix="/api/v1")
app.include_router(ws_write_router.ws_router)
app.include_router(skills_router.router, prefix="/api/v1")
app.include_router(extension_router.router, prefix="/api/v1")
app.include_router(dashboard_router.router, prefix="/api/v1")


@app.get("/")
async def root():
    """健康检查。"""
    return error(0, "ok", {"service": "story-rag", "docs": "/docs"})
