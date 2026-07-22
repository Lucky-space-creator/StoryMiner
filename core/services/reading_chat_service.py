"""
阅读辅助对话服务（M3 阅读对话）

整体思路：
    接收用户一条输入（文本 + 可选附件），组织 system + 历史 + 当前输入，调用 LLM 流式输出；
    在发送前按 context_window 评估历史体量，超出则把早期的对话压缩为摘要，控制 token 成本；
    全程以异步生成器向外逐步吐字（供 WebSocket 转发），结束后落库用户/助手两条消息。

关键点：
    1. 会话隔离：所有读写以 (user_id, novel_id) 为键，天然实现用户/小说级隔离。
    2. 上下文压缩：keep_recent 表示始终保留的最近消息条数；其余历史若超出 context_window
       token 预算，则合并为 compressed_summary 注入 system 区。
    3. 模型选择：优先用会话绑定的 llm_config_id，未绑定则回落到用户默认 chat 配置。
    4. 流式：chat_stream 为异步生成器，逐块转发 delta，异常转 to_user_error 文案。

实现逻辑：
    send_message 协程：开库会话 -> 取/建会话 -> 取历史 -> 估算 token ->
    超出则压缩 -> 组装 messages -> chat_stream 循环吐字 -> 累加内容 -> 落库 -> 提交。
"""
from typing import AsyncGenerator, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import SessionLocal
from common.crypto import decrypt
from models.llm_config import LLMConfig
from models.novel_content import Novel
from repositories.llm_repo import list_for_dispatch, get_owned
from services.llm_adapters import get_adapter
from common.task_errors import to_user_error
from repositories.reading_chat_repo import (
    get_or_create_session,
    list_messages,
    add_message,
    update_settings,
    update_compressed_summary,
    mark_compressed,
)


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：中英文混排按字符数 / 4 估算（无 tiktoken 依赖）。"""
    return max(1, len(text or "") // 4)


async def _resolve_llm_config(
    session: AsyncSession, owner_id: int, conv
) -> Optional[LLMConfig]:
    """解析本次应使用的 LLM 配置：会话绑定优先，否则用户默认 chat 配置。"""
    cfg_id = getattr(conv, "llm_config_id", None)
    if cfg_id is not None:
        return await get_owned(session, owner_id, cfg_id)
    items = await list_for_dispatch(session, owner_id, "chat")
    return items[0] if items else None


async def _build_system_prompt(
    session: AsyncSession, novel_id: int, conv
) -> str:
    """构造系统提示：基于小说标题与 AI 摘要，叠加用户自定义提示。"""
    row = (
        await session.execute(
            select(Novel.id, Novel.name, Novel.ai_summary).where(
                Novel.id == novel_id
            )
        )
    ).first()
    title = row.name if row else ""
    ai_summary = row.ai_summary if row else None
    parts = [
        "你是一位善于陪伴读者精读小说的 AI 阅读助手。",
        f"当前小说：《{title}》。" if title else "",
        "请基于小说正文与已知设定回答读者的问题，引用原文时标明章节；"
        "不编造未出现过的情节；若信息不足，请如实说明。",
    ]
    if ai_summary:
        parts.append(f"小说背景概要（供参考）：\n{ai_summary}")
    custom = getattr(conv, "system_prompt", None)
    if custom:
        parts.append(custom)
    return "\n".join(p for p in parts if p)


async def _maybe_compress(session, conv, messages):
    """历史超窗口时压缩：保留最近 keep_recent 条，其余合并为摘要。"""
    keep = conv.keep_recent or 10
    recent = messages[-keep:] if keep > 0 else messages
    to_compress = messages[:-keep] if keep > 0 else []
    summary = conv.compressed_summary or ""
    if not to_compress:
        return recent, summary

    cfg = await _resolve_llm_config(session, conv.owner_id, conv)
    if not cfg:
        return recent, summary
    base = (summary + "\n") if summary else ""
    prompt = (
        "请将以下对话历史压缩为一段简明摘要，保留关键情节、人物与读者关注点，"
        "不要展开新内容：\n"
        + "\n".join(f"{m.role}: {m.content}" for m in to_compress)
    )
    try:
        chunks = []
        adapter = get_adapter(cfg, decrypt(cfg.api_key) if cfg.api_key else None)
        async for chunk in adapter.chat_stream(
            [{"role": "user", "content": prompt}], temperature=0.3
        ):
            chunks.append(chunk)
        new_summary = (base + "".join(chunks)).strip()
        await update_compressed_summary(session, conv.id, new_summary)
        await mark_compressed(session, [m.id for m in to_compress])
        summary = new_summary
    except Exception:  # noqa: BLE001 压缩失败不阻断主流程，保留原摘要
        pass
    return recent, summary


async def send_message(
    user_id: int,
    novel_id: int,
    content: str,
    attachments: Optional[list] = None,
    llm_config_id: Optional[int] = None,
) -> AsyncGenerator[dict, None]:
    """流式发送一条阅读对话消息。

    yield 字典：
        {"type": "delta", "text": "..."}  流式增量
        {"type": "done", "user_message_id": int}
        {"type": "error", "message": str}
    """
    async with SessionLocal() as session:
        conv = await get_or_create_session(session, user_id, novel_id)

        cfg = await _resolve_llm_config(session, user_id, conv)
        if cfg is None:
            yield {"type": "error", "message": "未配置可用的对话模型，请先在设置中绑定。"}
            return

        # 落库用户消息（绑定模型时回写设置）
        if llm_config_id is not None:
            await update_settings(session, conv.id, llm_config_id=llm_config_id)
        user_msg = await add_message(
            session, conv.id, "user", content, attachments=attachments
        )
        await session.commit()

        # 取历史并按需压缩
        messages = await list_messages(session, conv.id)
        recent, summary = await _maybe_compress(session, conv, messages)

        # 组装发给 LLM 的消息：系统提示置首，压缩摘要作为第二条 system，再接历史与当前
        system_prompt = await _build_system_prompt(session, novel_id, conv)
        llm_messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if summary:
            llm_messages.append(
                {"role": "system", "content": "以下为先前对话的压缩摘要：\n" + summary}
            )
        for m in recent:
            llm_messages.append({"role": m.role, "content": m.content})
        llm_messages.append({"role": "user", "content": content})

        # 构造适配器（api_key 需先解密）；chat_stream 为实例方法，逐块 yield
        adapter = get_adapter(cfg, decrypt(cfg.api_key) if cfg.api_key else None)
        assistant_parts: list[str] = []
        try:
            async for chunk in adapter.chat_stream(llm_messages, temperature=0.7):
                assistant_parts.append(chunk)
                yield {"type": "delta", "text": chunk}
        except Exception as e:  # noqa: BLE001 异常转用户可读文案
            await session.rollback()
            yield {"type": "error", "message": to_user_error(e)}
            return

        # 落库助手消息
        assistant_content = "".join(assistant_parts)
        await add_message(
            session,
            conv.id,
            "assistant",
            assistant_content,
            tokens=estimate_tokens(assistant_content),
        )
        await session.commit()
        yield {"type": "done", "user_message_id": user_msg.id}
