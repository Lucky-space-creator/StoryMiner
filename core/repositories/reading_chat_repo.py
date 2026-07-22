"""
阅读辅助对话仓储（M3 阅读对话）

整体思路：
    围绕已存在的 story_conversation / story_conversation_message 两张表做会话级读写；
    一张会话按 (owner_id, novel_id) 唯一，进入同一小说即恢复历史，保证用户/小说级隔离。

关键点：
    1. get_or_create_session 按 (owner_id, novel_id) upsert，首次进入即恢复历史。
    2. list_messages 仅返回未压缩消息（is_compressed=False），压缩摘要走会话字段。
    3. context_window / keep_recent / compressed_summary 为上下文压缩三要素，由服务层计算后回写。
    4. 全部方法接收外部传入的 AsyncSession，事务由调用方控制，便于服务层统一提交。

实现逻辑：
    get_or_create_session: 先查后插（无并发竞争，阅读对话单用户串行）；
    add_message: 构造 ReadingChatMessage 并 add/flush 拿到 id；
    compress: 将超过窗口的旧消息标记为 is_compressed 并写入会话摘要。
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.reading_chat import ReadingConversation, ReadingChatMessage


async def get_or_create_session(
    session: AsyncSession,
    owner_id: int,
    novel_id: int,
    title: Optional[str] = None,
) -> ReadingConversation:
    """按 (owner_id, novel_id) 获取或创建会话；标题取小说相关默认值。"""
    stmt = select(ReadingConversation).where(
        ReadingConversation.owner_id == owner_id,
        ReadingConversation.novel_id == novel_id,
    )
    conv = (await session.execute(stmt)).scalar_one_or_none()
    if conv is not None:
        return conv
    conv = ReadingConversation(owner_id=owner_id, novel_id=novel_id, title=title)
    session.add(conv)
    await session.flush()
    await session.commit()
    return conv


async def get_session(
    session: AsyncSession, owner_id: int, novel_id: int
) -> Optional[ReadingConversation]:
    """获取会话（不存在返回 None）。"""
    stmt = select(ReadingConversation).where(
        ReadingConversation.owner_id == owner_id,
        ReadingConversation.novel_id == novel_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_messages(
    session: AsyncSession, conv_id: int, limit: int = 200
) -> list[ReadingChatMessage]:
    """列出会话内未压缩消息，按时间正序。"""
    stmt = (
        select(ReadingChatMessage)
        .where(
            ReadingChatMessage.conv_id == conv_id,
            ReadingChatMessage.is_compressed.is_(False),
        )
        .order_by(ReadingChatMessage.created_at.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def count_messages(session: AsyncSession, conv_id: int) -> int:
    """统计未压缩消息数。"""
    stmt = (
        select(func.count())
        .select_from(ReadingChatMessage)
        .where(
            ReadingChatMessage.conv_id == conv_id,
            ReadingChatMessage.is_compressed.is_(False),
        )
    )
    return int((await session.execute(stmt)).scalar_one() or 0)


async def add_message(
    session: AsyncSession,
    conv_id: int,
    role: str,
    content: str,
    attachments: Optional[list] = None,
    tokens: Optional[int] = None,
    character_id: Optional[int] = None,
) -> ReadingChatMessage:
    """新增一条消息并 flush（拿到自增 id）。"""
    msg = ReadingChatMessage(
        conv_id=conv_id,
        role=role,
        content=content,
        attachments=attachments or [],
        character_id=character_id,
        tokens=tokens or 0,
    )
    session.add(msg)
    await session.flush()
    return msg


async def update_settings(
    session: AsyncSession,
    conv_id: int,
    llm_config_id: Optional[int] = None,
    keep_recent: Optional[int] = None,
    context_window: Optional[int] = None,
    system_prompt: Optional[str] = None,
) -> None:
    """回写会话设置（模型、保留条数、上下文窗口、系统提示）。"""
    values: dict = {}
    if llm_config_id is not None:
        values["llm_config_id"] = llm_config_id
    if keep_recent is not None:
        values["keep_recent"] = keep_recent
    if context_window is not None:
        values["context_window"] = context_window
    if system_prompt is not None:
        values["system_prompt"] = system_prompt
    if values:
        values["updated_at"] = datetime.now(timezone.utc)
        await session.execute(
            update(ReadingConversation)
            .where(ReadingConversation.id == conv_id)
            .values(**values)
        )


async def update_compressed_summary(
    session: AsyncSession, conv_id: int, summary: str
) -> None:
    """写入压缩摘要并打时间戳。"""
    await session.execute(
        update(ReadingConversation)
        .where(ReadingConversation.id == conv_id)
        .values(
            compressed_summary=summary,
            updated_at=datetime.now(timezone.utc),
        )
    )


async def mark_compressed(session: AsyncSession, msg_ids: list[int]) -> None:
    """将指定旧消息标记为已压缩（从活跃上下文移除，但保留可查）。"""
    if not msg_ids:
        return
    await session.execute(
        update(ReadingChatMessage)
        .where(ReadingChatMessage.id.in_(msg_ids))
        .values(is_compressed=True)
    )


async def clear_messages(session: AsyncSession, conv_id: int) -> None:
    """清空会话消息（保留会话记录本身），并清空压缩摘要。"""
    await session.execute(
        update(ReadingChatMessage)
        .where(ReadingChatMessage.conv_id == conv_id)
        .values(is_compressed=True)
    )
    await update_compressed_summary(session, conv_id, "")
