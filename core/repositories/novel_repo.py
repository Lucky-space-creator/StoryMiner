"""
小说与内容数据访问（M1）

整体思路：
    封装 Novel/Chapter/Document/ParseTask/KnowledgeBase 的读写，所有查询强制 owner 隔离与逻辑删除过滤。

关键点：
    1. 列表/详情默认过滤 deleted_at IS NULL。
    2. 提供 get_or_create_default_kb 供上传时绑定知识库（满足 document.kb_id 非空约束）。
    3. 章节按 chapter_no, id 排序；合并用 update 批量逻辑删除。

实现逻辑：
    基于 async session 的 select/update；批量插入用 add_all。
"""
from datetime import datetime, timezone

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.novel_content import (
    Novel, Chapter, Document, ParseTask, KnowledgeBase,
)


async def create_novel(session: AsyncSession, novel: Novel) -> Novel:
    """写入小说（调用方提交）。"""
    session.add(novel)
    await session.flush()
    return novel


async def list_novels(session: AsyncSession, owner_id: int, page: int, size: int):
    """分页查询用户小说，返回 (items, total)。"""
    base = select(Novel).where(Novel.owner_id == owner_id, Novel.deleted_at.is_(None))
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    res = await session.execute(
        base.order_by(Novel.id.desc()).limit(size).offset((page - 1) * size)
    )
    return res.scalars().all(), total


async def get_novel(session: AsyncSession, owner_id: int, novel_id: int) -> Novel | None:
    """按归属查询小说（过滤逻辑删除）。"""
    n = await session.get(Novel, novel_id)
    if n and n.owner_id == owner_id and n.deleted_at is None:
        return n
    return None


async def soft_delete_novel(session: AsyncSession, novel: Novel) -> None:
    """逻辑删除小说。"""
    novel.deleted_at = datetime.now(timezone.utc)


async def count_chapters(session: AsyncSession, novel_id: int) -> int:
    """统计小说未删除章节数。"""
    stmt = select(func.count()).select_from(Chapter).where(
        Chapter.novel_id == novel_id, Chapter.deleted_at.is_(None)
    )
    return (await session.execute(stmt)).scalar() or 0


async def list_chapters(session: AsyncSession, novel_id: int, page: int = 1, size: int = 20, q: str | None = None) -> tuple[list[Chapter], int]:
    """分页查询小说章节（按章节号、ID 排序），支持标题模糊搜索。"""
    base = select(Chapter).where(
        Chapter.novel_id == novel_id, Chapter.deleted_at.is_(None)
    )
    if q:
        base = base.where(Chapter.title.ilike(f"%{q}%"))
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    res = await session.execute(
        base.order_by(Chapter.chapter_no, Chapter.id).limit(size).offset((page - 1) * size)
    )
    return list(res.scalars().all()), total


async def get_chapter(session: AsyncSession, chapter_id: int) -> Chapter | None:
    """按 ID 查询章节（含已删除，归属由 service 校验）。"""
    return await session.get(Chapter, chapter_id)


async def add_chapters(session: AsyncSession, chapters: list[Chapter]) -> None:
    """批量写入章节。"""
    session.add_all(chapters)
    await session.flush()


async def soft_delete_chapters(session: AsyncSession, ids: list[int]) -> None:
    """批量逻辑删除章节。"""
    await session.execute(
        update(Chapter)
        .where(Chapter.id.in_(ids))
        .values(deleted_at=datetime.now(timezone.utc))
    )


async def create_document(session: AsyncSession, doc: Document) -> Document:
    """写入文档（调用方提交）。"""
    session.add(doc)
    await session.flush()
    return doc


async def get_document(session: AsyncSession, doc_id: int) -> Document | None:
    """按 ID 查询文档。"""
    return await session.get(Document, doc_id)


async def find_document_by_hash(session: AsyncSession, novel_id: int, digest: str) -> Document | None:
    """按内容哈希查重（M1.8 去重）。"""
    stmt = select(Document).where(
        Document.novel_id == novel_id,
        Document.file_hash == digest,
        Document.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalars().first()


async def create_parse_task(session: AsyncSession, task: ParseTask) -> ParseTask:
    """写入解析任务（调用方提交）。"""
    session.add(task)
    await session.flush()
    return task


async def get_parse_task(session: AsyncSession, task_id: int) -> ParseTask | None:
    """按 ID 查询解析任务。"""
    return await session.get(ParseTask, task_id)


async def list_parse_tasks(session: AsyncSession, owner_id: int) -> list[ParseTask]:
    """查询用户解析任务列表（按 ID 倒序）。"""
    stmt = select(ParseTask).where(ParseTask.owner_id == owner_id).order_by(ParseTask.id.desc())
    return list((await session.execute(stmt)).scalars().all())


async def get_or_create_default_kb(session: AsyncSession, novel_id: int, owner_id: int) -> KnowledgeBase:
    """获取或创建小说默认知识库「正文库」（满足 document.kb_id 约束）。"""
    stmt = select(KnowledgeBase).where(
        KnowledgeBase.novel_id == novel_id,
        KnowledgeBase.name == "正文库",
        KnowledgeBase.deleted_at.is_(None),
    )
    existing = (await session.execute(stmt)).scalars().first()
    if existing:
        return existing
    kb = KnowledgeBase(novel_id=novel_id, owner_id=owner_id, name="正文库", scope="private")
    session.add(kb)
    await session.flush()
    return kb
