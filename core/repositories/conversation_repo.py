"""
对话数据访问（M7）

整体思路：
    封装 story_conversation 与 story_conversation_message 的读写，会话级强制 owner 隔离，
    删除会话级联清理其消息；消息按 conv 归属顺序读取，支撑多轮历史还原。

关键点：
    1. 列表/详情默认按 owner_id 过滤；get_conv 校验归属后返回，越权返回 None。
    2. delete_conv 先清消息再清会话，保证外键一致。
    3. add_message 由调用方统一提交；list_messages 按 id 升序还原时间线。

实现逻辑：
    基于 async session 的 select/delete；写入后 flush 取回自增 ID。
"""
from sqlalchemy import select, func, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from models.conversation import Conversation, ConversationMessage


async def create_conv(session: AsyncSession, conv: Conversation) -> Conversation:
    """写入会话（调用方提交）。"""
    session.add(conv)
    await session.flush()
    return conv


async def list_convs(session: AsyncSession, owner_id: int, page: int = 1, size: int = 50):
    """分页查询当前用户会话（按 id 倒序），返回 (items, total)。"""
    base = select(Conversation).where(Conversation.owner_id == owner_id)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    rows = (await session.execute(
        base.order_by(Conversation.id.desc()).limit(size).offset((page - 1) * size)
    )).scalars().all()
    return list(rows), total


async def get_conv(session: AsyncSession, owner_id: int, conv_id: int) -> Conversation | None:
    """按归属查询会话，越权或不存在返回 None。"""
    conv = await session.get(Conversation, conv_id)
    if conv and conv.owner_id == owner_id:
        return conv
    return None


async def delete_conv(session: AsyncSession, conv_id: int) -> None:
    """级联删除会话及其全部消息。"""
    await session.execute(delete(ConversationMessage).where(ConversationMessage.conv_id == conv_id))
    await session.execute(delete(Conversation).where(Conversation.id == conv_id))


async def add_message(session: AsyncSession, msg: ConversationMessage) -> ConversationMessage:
    """写入消息（调用方提交）。"""
    session.add(msg)
    await session.flush()
    return msg


async def list_messages(session: AsyncSession, conv_id: int) -> list[ConversationMessage]:
    """读取会话全部消息（按时间升序），用于多轮历史还原。"""
    stmt = select(ConversationMessage).where(
        ConversationMessage.conv_id == conv_id
    ).order_by(ConversationMessage.id)
    return list((await session.execute(stmt)).scalars().all())


async def set_favorite(session: AsyncSession, owner_id: int, conv_id: int, favorite: bool) -> None:
    """切换会话收藏状态（校验归属）。"""
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conv_id, Conversation.owner_id == owner_id)
        .values(favorite=favorite)
    )
