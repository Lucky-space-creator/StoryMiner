"""
人物档案数据访问（M6）

整体思路：
    封装 story_character 的读写，所有查询强制 owner 隔离与逻辑删除过滤，
    提供按小说列表、按 ID/名称查询、写入与软删除能力，支撑人物卡 CRUD。

关键点：
    1. 列表/详情默认过滤 deleted_at IS NULL。
    2. 按 (novel_id, name) 查询用于新建去重；按 ID + owner 查询用于归属校验。

实现逻辑：
    基于 async session 的 select/update；写入后 flush 取回自增 ID，调用方统一提交。
"""
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.character import Character


async def list_by_novel(session: AsyncSession, novel_id: int) -> list[Character]:
    """查询小说全部人物（过滤逻辑删除），按 ID 排序。"""
    stmt = select(Character).where(
        Character.novel_id == novel_id, Character.deleted_at.is_(None)
    ).order_by(Character.id)
    return list((await session.execute(stmt)).scalars().all())


async def get(session: AsyncSession, char_id: int) -> Character | None:
    """按 ID 查询人物（含已删除，归属由 service 校验）。"""
    return await session.get(Character, char_id)


async def get_by_novel_name(session: AsyncSession, novel_id: int, name: str) -> Character | None:
    """按 (novel_id, name) 查人物（过滤逻辑删除），用于新建去重。"""
    stmt = select(Character).where(
        Character.novel_id == novel_id,
        Character.name == name,
        Character.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalars().first()


async def create(session: AsyncSession, c: Character) -> Character:
    """写入人物（调用方提交）。"""
    session.add(c)
    await session.flush()
    return c


async def soft_delete(session: AsyncSession, char_id: int) -> None:
    """逻辑删除人物。"""
    from datetime import datetime, timezone
    await session.execute(
        update(Character)
        .where(Character.id == char_id)
        .values(deleted_at=datetime.now(timezone.utc))
    )
