"""
续写数据访问层（M8.6/M8.7）

整体思路：
    封装 story_continue_write 表的 CRUD，服务于多版本列表、采纳入库前的查询。

关键点：
    1. 全部按 owner_id 隔离；列表按创建时间倒序（最新版本在前）。
    2. save_version 写入并返回实例；get_version 校验归属后返回。

实现逻辑：
    纯 SQLAlchemy 异步查询；逻辑删除走 deleted_at 过滤。
"""
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from models.story_writing import ContinueWrite


async def save_version(session: AsyncSession, obj: ContinueWrite) -> ContinueWrite:
    """写入一条续写版本并返回。"""
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return obj


async def list_versions(session: AsyncSession, owner_id: int, novel_id: int) -> list[ContinueWrite]:
    """续写版本列表（M8.6）：按小说 + 用户，创建时间倒序。"""
    stmt = (
        select(ContinueWrite)
        .where(
            ContinueWrite.owner_id == owner_id,
            ContinueWrite.novel_id == novel_id,
            ContinueWrite.deleted_at.is_(None),
        )
        .order_by(desc(ContinueWrite.created_at))
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_version(session: AsyncSession, owner_id: int, version_id: int) -> ContinueWrite | None:
    """按 id 校验归属后取版本（M8.7 采纳用）。"""
    obj = await session.get(ContinueWrite, version_id)
    if not obj or obj.owner_id != owner_id or obj.deleted_at is not None:
        return None
    return obj
