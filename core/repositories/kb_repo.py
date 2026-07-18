"""
知识库数据访问（M2 / 方案A）

整体思路：
    封装 KnowledgeBase 的读写与 KB 维度的文档/切片统计、级联清理，所有查询强制 owner 隔离与逻辑删除过滤。
    方案A 下，文档与知识库解耦：文档归属小说，知识库通过 story_kb_document 链接表关联文档；
    列表/统计/删除均基于链接表，文档本身不再随知识库删除而删除。

关键点：
    1. KB/文档列表默认过滤 deleted_at IS NULL；切片（story_chunk 无 deleted_at）为物理删除。
    2. list_documents/count_documents/sum_word_count 改为 JOIN story_kb_document，按链接关系过滤。
    3. 建库受 UNIQUE(novel_id, name) 约束，服务层需先查重。
    4. 统计返回文档数、切片数、字符量；删除 KB 时仅清链接与切片，不触碰文档（文档归小说所有）。

实现逻辑：
    基于 async session 的 select/update/delete；文档复用 novel_repo 的通用文档访问；链接表读写本文件内聚。
"""
from datetime import datetime, timezone

from sqlalchemy import select, func, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from models.novel_content import KnowledgeBase, Document, KbDocument, ParseTask
from models.chunk import Chunk


async def create_kb(session: AsyncSession, kb: KnowledgeBase) -> KnowledgeBase:
    """写入知识库（调用方提交）。"""
    session.add(kb)
    await session.flush()
    return kb


async def exists_name(session: AsyncSession, novel_id: int, name: str) -> bool:
    """校验同一小说下知识库名是否已存在（对齐 UNIQUE 约束）。"""
    stmt = select(func.count()).select_from(KnowledgeBase).where(
        KnowledgeBase.novel_id == novel_id,
        KnowledgeBase.name == name,
        KnowledgeBase.deleted_at.is_(None),
    )
    return ((await session.execute(stmt)).scalar() or 0) > 0


async def list_kbs(session: AsyncSession, owner_id: int, novel_id: int | None, page: int, size: int):
    """分页查询知识库（可按 novel_id 过滤），返回 (items, total)。"""
    base = select(KnowledgeBase).where(
        KnowledgeBase.owner_id == owner_id, KnowledgeBase.deleted_at.is_(None)
    )
    if novel_id is not None:
        base = base.where(KnowledgeBase.novel_id == novel_id)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    res = await session.execute(base.order_by(KnowledgeBase.id.desc()).limit(size).offset((page - 1) * size))
    return res.scalars().all(), total


async def get_kb(session: AsyncSession, owner_id: int, kb_id: int) -> KnowledgeBase | None:
    """按归属查询知识库（过滤逻辑删除）。"""
    kb = await session.get(KnowledgeBase, kb_id)
    if kb and kb.owner_id == owner_id and kb.deleted_at is None:
        return kb
    return None


async def hard_delete_kb(session: AsyncSession, kb_id: int) -> None:
    """物理删除知识库（级联清理已在 cascade_delete 完成，此处删 kb 行）。"""
    await session.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))


async def add_kb_document(session: AsyncSession, kb_id: int, doc_id: int, owner_id: int) -> None:
    """建立知识库-文档关联（幂等：已存在则跳过）。"""
    exists = (await session.execute(
        select(KbDocument).where(KbDocument.kb_id == kb_id, KbDocument.doc_id == doc_id)
    )).scalars().first()
    if exists:
        return
    session.add(KbDocument(kb_id=kb_id, doc_id=doc_id, owner_id=owner_id))
    await session.flush()


async def remove_kb_document(session: AsyncSession, kb_id: int, doc_id: int) -> None:
    """移除单条知识库-文档关联。"""
    await session.execute(
        delete(KbDocument).where(KbDocument.kb_id == kb_id, KbDocument.doc_id == doc_id)
    )


async def list_kb_document_ids(session: AsyncSession, kb_id: int) -> set[int]:
    """该知识库已关联文档 id 集合。"""
    stmt = select(KbDocument.doc_id).where(KbDocument.kb_id == kb_id)
    return {r[0] for r in (await session.execute(stmt)).all()}


async def list_documents(session: AsyncSession, kb_id: int, page: int, size: int):
    """分页查询知识库下文档（基于链接表），返回 (items, total)。"""
    base = (
        select(Document)
        .join(KbDocument, KbDocument.doc_id == Document.id)
        .where(KbDocument.kb_id == kb_id, Document.deleted_at.is_(None))
    )
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    res = await session.execute(base.order_by(Document.id.desc()).limit(size).offset((page - 1) * size))
    return res.scalars().all(), total


async def hard_delete_document(session: AsyncSession, doc_id: int) -> None:
    """物理删除文档：移除其在所有知识库的关联，并物理删除其解析任务/切片与文档行。"""
    await session.execute(delete(KbDocument).where(KbDocument.doc_id == doc_id))
    await session.execute(delete(Chunk).where(Chunk.doc_id == doc_id))
    await session.execute(delete(ParseTask).where(ParseTask.doc_id == doc_id))
    await session.execute(delete(Document).where(Document.id == doc_id))


async def count_documents(session: AsyncSession, kb_id: int) -> int:
    """统计知识库未删除文档数（基于链接表）。"""
    stmt = (
        select(func.count()).select_from(Document)
        .join(KbDocument, KbDocument.doc_id == Document.id)
        .where(KbDocument.kb_id == kb_id, Document.deleted_at.is_(None))
    )
    return (await session.execute(stmt)).scalar() or 0


async def count_chunks(session: AsyncSession, kb_id: int) -> int:
    """统计知识库切片数。"""
    stmt = select(func.count()).select_from(Chunk).where(Chunk.kb_id == kb_id)
    return (await session.execute(stmt)).scalar() or 0


async def sum_word_count(session: AsyncSession, kb_id: int) -> int:
    """统计知识库未删除文档字符量之和（基于链接表）。"""
    stmt = (
        select(func.coalesce(func.sum(Document.word_count), 0))
        .select_from(Document)
        .join(KbDocument, KbDocument.doc_id == Document.id)
        .where(KbDocument.kb_id == kb_id, Document.deleted_at.is_(None))
    )
    return (await session.execute(stmt)).scalar() or 0


async def list_chunks(session: AsyncSession, kb_id: int) -> list[Chunk]:
    """查询知识库全部切片（导出用，按 idx 排序）。"""
    stmt = select(Chunk).where(Chunk.kb_id == kb_id).order_by(Chunk.doc_id, Chunk.idx)
    return list((await session.execute(stmt)).scalars().all())


async def cascade_delete(session: AsyncSession, kb_id: int) -> None:
    """删除知识库时级联：移除关联链接、物理删除切片。文档归小说所有，不删除。"""
    await session.execute(delete(KbDocument).where(KbDocument.kb_id == kb_id))
    await session.execute(delete(Chunk).where(Chunk.kb_id == kb_id))
