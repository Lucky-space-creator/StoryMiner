"""
扩展功能数据访问（M13）。

整体思路：
    集中 M13 各表（笔记/标签/收藏/审计/阅读进度）的 CRUD 与全局搜索所需的跨表查询；
    事务由上层 service 控制，所有查询强制 owner_id 隔离，杜绝越权。

关键点：
    1. 全局搜索(M13.1) 对 Novel/Chapter/Chunk/Character 做 ILIKE 关键词匹配，各限量后合并。
    2. 笔记/收藏/标签均按 owner 过滤，列表按创建时间倒序。
    3. 审计日志只提供追加与倒序列表（只增不改）。

实现逻辑：
    使用 SQLAlchemy 2.0 async 语法；搜索用 ilike 通配，字段命中即返回摘要。
"""
from sqlalchemy import select, delete, or_

from models.extension import Note, Tag, TagLink, Favorite, AuditLog, ReadingProgress
from models.novel_content import Novel, Chapter, KnowledgeBase
from models.chunk import Chunk
from models.character import Character


# ---------------- 笔记（M13.2） ----------------
async def list_notes(session, owner_id: int):
    """列出当前用户全部笔记，按更新时间倒序。"""
    stmt = select(Note).where(Note.owner_id == owner_id).order_by(Note.updated_at.desc())
    return (await session.execute(stmt)).scalars().all()


async def create_note(session, owner_id: int, data: dict):
    """新增一条笔记。"""
    obj = Note(owner_id=owner_id, **data)
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return obj


async def delete_note(session, owner_id: int, note_id: int):
    """删除笔记（仅本人）。"""
    await session.execute(delete(Note).where(Note.id == note_id, Note.owner_id == owner_id))


# ---------------- 标签（M13.4） ----------------
async def list_tags(session, owner_id: int):
    """列出当前用户全部标签。"""
    stmt = select(Tag).where(Tag.owner_id == owner_id).order_by(Tag.id.desc())
    return (await session.execute(stmt)).scalars().all()


async def create_tag(session, owner_id: int, data: dict):
    """新增标签。"""
    obj = Tag(owner_id=owner_id, **data)
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return obj


async def get_tag_by_name(session, owner_id: int, name: str):
    """按名称取标签（用于去重）。"""
    stmt = select(Tag).where(Tag.owner_id == owner_id, Tag.name == name)
    return (await session.execute(stmt)).scalar_one_or_none()


# ---------------- 收藏（M13.6） ----------------
async def list_favorites(session, owner_id: int):
    """列出当前用户全部收藏，按创建时间倒序。"""
    stmt = select(Favorite).where(Favorite.owner_id == owner_id).order_by(Favorite.created_at.desc())
    return (await session.execute(stmt)).scalars().all()


async def create_favorite(session, owner_id: int, data: dict):
    """新增收藏。"""
    obj = Favorite(owner_id=owner_id, **data)
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return obj


async def delete_favorite(session, owner_id: int, fav_id: int):
    """取消收藏（仅本人）。"""
    await session.execute(delete(Favorite).where(Favorite.id == fav_id, Favorite.owner_id == owner_id))


# ---------------- 审计日志（M13.7） ----------------
async def list_audit_logs(session, owner_id: int, limit: int = 100):
    """列出审计日志，倒序，默认最近 100 条。"""
    stmt = (
        select(AuditLog).where(AuditLog.owner_id == owner_id)
        .order_by(AuditLog.created_at.desc()).limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


async def add_audit_log(session, owner_id: int, action: str, target: str, detail: dict | None = None):
    """追加一条审计日志（只增不改）。"""
    obj = AuditLog(owner_id=owner_id, action=action, target=target, detail=detail or {})
    session.add(obj)
    await session.flush()
    return obj


# ---------------- 阅读进度（M13.3） ----------------
async def get_progress(session, owner_id: int, novel_id: int):
    """取某本小说的阅读进度。"""
    stmt = select(ReadingProgress).where(
        ReadingProgress.owner_id == owner_id, ReadingProgress.novel_id == novel_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def upsert_progress(session, owner_id: int, novel_id: int, chapter_id: int | None, position: int):
    """写入/更新阅读进度（按 owner+novel 唯一）。"""
    obj = await get_progress(session, owner_id, novel_id)
    if obj:
        obj.chapter_id = chapter_id
        obj.position = position
    else:
        obj = ReadingProgress(
            owner_id=owner_id, novel_id=novel_id, chapter_id=chapter_id, position=position
        )
        session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return obj


# ---------------- 全局搜索（M13.1） ----------------
async def search_novels(session, owner_id: int, kw: str, limit: int = 10):
    """搜索小说：书名/作者/简介命中。"""
    like = f"%{kw}%"
    stmt = (
        select(Novel)
        .where(Novel.owner_id == owner_id, Novel.deleted_at.is_(None))
        .where(or_(Novel.name.ilike(like), Novel.author.ilike(like), Novel.description.ilike(like)))
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


async def search_chapters(session, owner_id: int, kw: str, limit: int = 10):
    """搜索章节：标题命中（经 Novel 归属 owner）。"""
    like = f"%{kw}%"
    stmt = (
        select(Chapter).join(Novel, Chapter.novel_id == Novel.id)
        .where(Novel.owner_id == owner_id, Chapter.title.ilike(like))
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


async def search_chunks(session, owner_id: int, kw: str, limit: int = 10):
    """搜索切片：内容命中。"""
    like = f"%{kw}%"
    stmt = (
        select(Chunk).where(Chunk.owner_id == owner_id, Chunk.content.ilike(like)).limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


async def search_characters(session, owner_id: int, kw: str, limit: int = 10):
    """搜索人物：姓名/身份/简介命中。"""
    like = f"%{kw}%"
    stmt = (
        select(Character)
        .where(Character.owner_id == owner_id)
        .where(or_(Character.name.ilike(like), Character.identity.ilike(like), Character.description.ilike(like)))
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()
