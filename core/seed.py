"""
初始化数据库测试数据

整体思路：
    复用既有 db 引擎，幂等插入固定测试账号与默认嵌入模型配置，供前端登录联调
    与 M3 嵌入流程使用。数据已存在则跳过，保证脚本可重复执行。

关键点：
    1. 测试账号：admin/admin，密码经 hash_password 哈希，与登录校验逻辑一致。
    2. 默认嵌入模型：本地 Ollama 的 nomic-embed-text（无需联网/密钥），以全局配置
       (owner_id=None) 插入，对所有用户可见可用，M3 索引时由 list_for_dispatch 命中。
    3. 全部先查重再写入，避免重复插入。

实现逻辑：
    异步会话内先 seed 测试账号，再 seed 嵌入模型配置；均通过 repo 查重后提交。
"""
import asyncio

from db import SessionLocal
from models.user import User
from models.llm_config import LLMConfig
from repositories import user_repo, llm_repo
from auth.security import hash_password

# 测试账号（可按需修改）
TEST_USERNAME = "admin"
TEST_PASSWORD = "admin"
TEST_NAME = "测试用户"

# 默认嵌入模型：本地 Ollama，无需联网/密钥（nomic-embed-text 维度 768）
EMBED_NAME = "本地Ollama-nomic-embed-text"
EMBED_PROVIDER = "ollama"
EMBED_MODEL = "nomic-embed-text"
EMBED_BASE_URL = "http://localhost:11434"
EMBED_TIMEOUT = 120


async def _seed_user(session: SessionLocal) -> None:
    """幂等插入固定测试账号。"""
    exist = await user_repo.get_by_username(session, TEST_USERNAME)
    if exist:
        print(f"账号已存在，跳过：{TEST_USERNAME} (id={exist.id})")
        return
    user = User(
        username=TEST_USERNAME,
        password=hash_password(TEST_PASSWORD),
        name=TEST_NAME,
    )
    await user_repo.create(session, user)
    await session.commit()
    await session.refresh(user)
    print(f"已创建测试账号：{TEST_USERNAME} / {TEST_PASSWORD} (id={user.id})")


async def _seed_embed_config(session: SessionLocal) -> None:
    """幂等插入本地 Ollama 嵌入模型默认配置（全局可见）。"""
    # 取全局 + 任意 owner 的 embed 配置做查重（owner_id=0 仅作占位，必无此用户）
    existing = await llm_repo.list_configs(session, 0, "embed")
    if any(c.provider == EMBED_PROVIDER and c.model == EMBED_MODEL and c.owner_id is None
           for c in existing):
        print(f"嵌入模型配置已存在，跳过：{EMBED_PROVIDER}/{EMBED_MODEL}")
        return
    cfg = LLMConfig(
        owner_id=None,                # 全局配置，对所有用户可见可用
        name=EMBED_NAME,
        provider=EMBED_PROVIDER,
        model=EMBED_MODEL,
        base_url=EMBED_BASE_URL,
        api_key=None,                 # Ollama 无需密钥，crypto.decrypt(None) 安全透传
        llm_type="embed",
        is_default=True,
        weight=0,
        timeout=EMBED_TIMEOUT,
        status="active",
    )
    await llm_repo.create(session, cfg)
    await session.flush()
    print(f"已创建默认嵌入模型配置：{EMBED_PROVIDER}/{EMBED_MODEL} (id={cfg.id})")


# 默认对话模型：本地 Ollama qwen2.5:7b（M5 实体关系抽取依赖）
CHAT_NAME = "本地Ollama-qwen2.5:7b"
CHAT_PROVIDER = "ollama"
CHAT_MODEL = "qwen2.5:7b"
CHAT_BASE_URL = "http://localhost:11434"
CHAT_TIMEOUT = 300


async def _seed_chat_config(session: SessionLocal) -> None:
    """幂等插入本地 Ollama 对话模型默认配置（全局可见，M5 抽取使用）。"""
    existing = await llm_repo.list_configs(session, 0, "chat")
    if any(c.provider == CHAT_PROVIDER and c.model == CHAT_MODEL and c.owner_id is None
           for c in existing):
        print(f"对话模型配置已存在，跳过：{CHAT_PROVIDER}/{CHAT_MODEL}")
        return
    cfg = LLMConfig(
        owner_id=None,                # 全局配置，对所有用户可见可用
        name=CHAT_NAME,
        provider=CHAT_PROVIDER,
        model=CHAT_MODEL,
        base_url=CHAT_BASE_URL,
        api_key=None,                 # Ollama 无需密钥
        llm_type="chat",
        is_default=True,
        weight=0,
        timeout=CHAT_TIMEOUT,
        status="active",
    )
    await llm_repo.create(session, cfg)
    await session.flush()
    print(f"已创建默认对话模型配置：{CHAT_PROVIDER}/{CHAT_MODEL} (id={cfg.id})")


async def main() -> None:
    async with SessionLocal() as session:
        await _seed_user(session)
        await _seed_embed_config(session)
        await _seed_chat_config(session)
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
