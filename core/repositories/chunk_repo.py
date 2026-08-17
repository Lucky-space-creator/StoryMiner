"""
切片数据访问（M3 切割向量化 / M4 切片展示）

整体思路：
    封装 Chunk 的批量写入、按 KB 清理、增量判断与向量相似检索，所有查询带 kb 隔离。

关键点：
    1. delete_by_kb 全量重建前清空旧切片；indexed_doc_ids 支撑增量索引。
    2. similar 走向量库（Chroma）取 chunk id，再回 PG 组装业务字段，保证数据一致。
    3. 向量库按 kb 分 collection（kb_{kb_id}），业务字段仍由 PostgreSQL 承载。

实现逻辑：
    基于 async session 的 select/delete；相似检索委托 vectorstore 查询后按 id 回查。
"""
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.chunk import Chunk
from vectorstore.factory import get_vector_store


async def delete_by_kb(session: AsyncSession, kb_id: int) -> None:
    """清空知识库全部切片（重建索引前置）。"""
    await session.execute(delete(Chunk).where(Chunk.kb_id == kb_id))


async def indexed_doc_ids(session: AsyncSession, kb_id: int) -> set[int]:
    """已建索引的文档 id 集合（增量索引用）。"""
    stmt = select(Chunk.doc_id).where(Chunk.kb_id == kb_id)
    return {r[0] for r in (await session.execute(stmt)).all()}


async def bulk_insert(session: AsyncSession, chunks: list[Chunk]) -> None:
    """批量写入切片（调用方提交）。"""
    session.add_all(chunks)
    await session.flush()


async def similar(session: AsyncSession, kb_id: int, vector: list[float], dim: int, top_k: int = 5) -> list[dict]:
    """向量相似检索（M4）：查向量库取 chunk id，回 PG 组装业务字段。"""
    store = get_vector_store()
    hits = await store.query(collection=f"kb_{kb_id}", vector=vector, top_k=top_k, where={"dim": dim})
    if not hits:
        return []
    ids = [cid for cid, _ in hits]
    dist_map = {cid: d for cid, d in hits}
    stmt = select(Chunk).where(Chunk.id.in_(ids))
    rows = (await session.execute(stmt)).scalars().all()
    rows.sort(key=lambda r: dist_map[r.id])
    return [{
        "id": r.id, "doc_id": r.doc_id, "chapter_id": r.chapter_id,
        "idx": r.idx, "content": r.content, "meta": r.meta,
        "distance": dist_map[r.id],
    } for r in rows]


async def list_chunks(session, owner_id, kb_id=None, chapter_id=None, disabled=None, page=1, size=20):
    """分页查询切片（M4 列表）：按 owner 隔离 + 可选过滤。"""
    cond = [Chunk.owner_id == owner_id]
    if kb_id is not None:
        cond.append(Chunk.kb_id == kb_id)
    if chapter_id is not None:
        cond.append(Chunk.chapter_id == chapter_id)
    if disabled is not None:
        cond.append(Chunk.disabled == disabled)
    total = (await session.execute(select(func.count()).select_from(Chunk).where(*cond))).scalar_one()
    rows = (await session.execute(
        select(Chunk).where(*cond).order_by(Chunk.id.asc()).offset((page - 1) * size).limit(size)
    )).scalars().all()
    return rows, total


async def get_chunk(session, owner_id, chunk_id):
    """取单条切片并校验归属（M4 详情/编辑前置）。"""
    row = await session.get(Chunk, chunk_id)
    if row and row.owner_id != owner_id:
        return None
    return row


async def search_chunks(session, owner_id, q, page=1, size=20):
    """关键词检索切片（M4 检索）：content ILIKE。"""
    cond = [Chunk.owner_id == owner_id, Chunk.content.ilike(f"%{q}%")]
    total = (await session.execute(select(func.count()).select_from(Chunk).where(*cond))).scalar_one()
    rows = (await session.execute(
        select(Chunk).where(*cond).order_by(Chunk.id.asc()).offset((page - 1) * size).limit(size)
    )).scalars().all()
    return rows, total


async def set_disabled(session, owner_id, chunk_id, disabled):
    """屏蔽/启用切片（M4.7）。"""
    row = await get_chunk(session, owner_id, chunk_id)
    if not row:
        return None
    row.disabled = disabled
    await session.flush()
    return row


async def update_content(session, owner_id, chunk_id, content):
    """更新切片内容（M4.6 前置），同步字数。"""
    row = await get_chunk(session, owner_id, chunk_id)
    if not row:
        return None
    row.content = content
    row.word_count = len(content)
    await session.flush()
    return row
