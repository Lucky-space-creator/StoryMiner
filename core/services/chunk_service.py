"""
切片业务逻辑（M3 切割+向量化）

整体思路：
    编排「取文档→切章切片→嵌入→落库」，并支持全量重建与增量索引、切片策略管理，
    嵌入复用 M9 的 embed 适配器与默认模型分发链。

关键点：
    1. 嵌入模型取 owner 的 embed 类型默认配置（list_for_dispatch），缺失即报错引导配置。
    2. 向量写入向量库（Chroma，按 kb 分集合）；meta 记录 dim 与章节定位。
    3. 全量重建先删 PG 与向量库集合再写；增量仅处理未建索引文档。

实现逻辑：
    委托 chunker 切割、chunk_repo 存取、llm_repo/llm_adapters 嵌入；本层只做编排与字段映射。
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models.chunk import Chunk
from schemas.chunk import ChunkRequest
from repositories import kb_repo, chunk_repo, llm_repo
from services import chunker, llm_adapters
from common import crypto
from common.exceptions import BizError
from common.response import paginate
from storage import read as read_file, exists as exists_file
from vectorstore.factory import get_vector_store
from db import SessionLocal

# 切割进度（按 task_id 内存存储，供前端轮询）
_chunk_progress: dict[str, dict] = {}

def _set_chunk_progress(task_id: str, **kw) -> None:
    """更新切割任务进度。"""
    cur = _chunk_progress.setdefault(task_id, {})
    cur.update(kw)


def get_chunk_progress(task_id: str) -> dict | None:
    """读取切割任务进度。"""
    return _chunk_progress.get(task_id)


async def _get_embed_adapter(session: AsyncSession, owner_id: int):
    """取默认嵌入模型适配器（M9 分发链），返回 (adapter, model_name)。"""
    cfgs = await llm_repo.list_for_dispatch(session, owner_id, "embed")
    if not cfgs:
        raise BizError(400, "尚未配置嵌入模型（llm_type=embed），请先在模型管理中添加")
    cfg = cfgs[0]
    return llm_adapters.get_adapter(cfg, crypto.decrypt(cfg.api_key)), cfg.model


async def _index_documents(
    session: AsyncSession, owner_id: int, kb, docs: list, strategy: str, size: int, overlap: int,
    task_id: str | None = None,
) -> dict:
    """对给定文档执行切章→切片→嵌入→落库，返回统计；task_id 非空时按阶段发布进度。"""
    pieces_all: list[tuple] = []  # (doc, piece)
    if task_id:
        _set_chunk_progress(task_id, progress=5, stage="chunking", status="running")
    for doc in docs:
        if not doc.object_key or not await exists_file(doc.object_key):
            continue
        raw = await read_file(doc.object_key)
        text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
        for p in chunker.chunk_document(text, strategy, size, overlap):
            pieces_all.append((doc, p))
    if not pieces_all:
        if task_id:
            _set_chunk_progress(task_id, progress=100, stage="done", status="success",
                                message="无需切割（无文档内容）", chunk_count=0, doc_count=0, model=None, dim=0)
        return {"chunk_count": 0, "doc_count": 0, "model": None, "dim": 0}
    # 批量嵌入（默认模型适配器已就绪）
    if task_id:
        _set_chunk_progress(task_id, progress=20, stage="embedding", status="running")
    adapter, model = await _get_embed_adapter(session, owner_id)
    BATCH = 32
    texts = [p[1]["content"] for p in pieces_all]
    vectors_all: list[list[float]] = []
    total = len(texts)
    done = 0
    for i in range(0, total, BATCH):
        batch = texts[i:i + BATCH]
        vectors_all.extend(await adapter.embed(batch))
        done += len(batch)
        if task_id:
            prog = 20 + int(70 * done / total) if total else 90
            _set_chunk_progress(task_id, progress=prog, stage="embedding", status="running")
    dim = len(vectors_all[0]) if vectors_all else 0
    # 组装切片
    if task_id:
        _set_chunk_progress(task_id, progress=95, stage="storing", status="running")
    chunks: list[Chunk] = []
    for (doc, p), vec in zip(pieces_all, vectors_all):
        chunks.append(Chunk(
            doc_id=doc.id, novel_id=kb.novel_id, kb_id=kb.id, owner_id=owner_id,
            idx=len(chunks) + 1,
            content=p["content"], word_count=len(p["content"]),
            embedding=vec,
            meta={
                "chapter_title": p["title"],
                "char_start": p["char_start"],
                "char_end": p["char_end"],
                "dim": dim,
            },
        ))
    await chunk_repo.bulk_insert(session, chunks)
    # 写入向量库（Chroma 本地持久化；保留 Chunk.embedding 列便于后续切回 pgvector）
    store = get_vector_store()
    await store.upsert(
        collection=f"kb_{kb.id}",
        ids=[c.id for c in chunks],
        vectors=vectors_all,
        documents=[c.content for c in chunks],
        metadatas=[{"owner_id": owner_id, "dim": dim} for _ in chunks],
    )
    return {"chunk_count": len(chunks), "doc_count": len(docs), "model": model, "dim": dim}


async def chunk_and_index(session: AsyncSession, owner_id: int, kb_id: int, req: ChunkRequest, full: bool) -> dict:
    """触发切割+向量化（M3.1 / M3.3 重建）。"""
    kb = await kb_repo.get_kb(session, owner_id, kb_id)
    if not kb:
        raise BizError(404, "知识库不存在")
    docs, _ = await kb_repo.list_documents(session, kb_id, 1, 100000)
    if full:
        await chunk_repo.delete_by_kb(session, kb_id)
        # 清空向量库集合，避免重建后残留旧 chunk 向量
        store = get_vector_store()
        await store.delete_collection(f"kb_{kb_id}")
    stats = await _index_documents(session, owner_id, kb, docs, req.strategy, req.size, req.overlap)
    await session.commit()
    return {"kb_id": kb_id, **stats, "full": full}


async def chunk_and_index_async(owner_id: int, kb_id: int, req: ChunkRequest, full: bool, task_id: str) -> None:
    """后台切割+向量化（M3.1）：独立会话执行，按 batch 发布进度到内存存储。"""
    try:
        _set_chunk_progress(task_id, progress=0, stage="preparing", status="running")
        async with SessionLocal() as session:
            kb = await kb_repo.get_kb(session, owner_id, kb_id)
            if not kb:
                raise BizError(404, "知识库不存在")
            docs, _ = await kb_repo.list_documents(session, kb_id, 1, 100000)
            if full:
                await chunk_repo.delete_by_kb(session, kb_id)
                # 清空向量库集合，避免重建后残留旧 chunk 向量
                store = get_vector_store()
                await store.delete_collection(f"kb_{kb_id}")
            stats = await _index_documents(session, owner_id, kb, docs, req.strategy, req.size, req.overlap, task_id=task_id)
            await session.commit()
        _set_chunk_progress(task_id, progress=100, stage="done", status="success", message="切割完成", **stats)
    except Exception as e:  # 后台任务异常：回写失败状态，避免前端轮询卡住
        prev = _chunk_progress.get(task_id) or {}
        _set_chunk_progress(task_id, progress=prev.get("progress", 0), stage="failed", status="failed", error=str(e))


async def incremental_index(session: AsyncSession, owner_id: int, kb_id: int, req: ChunkRequest) -> dict:
    """仅增量索引新增/未索引文档（M3.4）。"""
    kb = await kb_repo.get_kb(session, owner_id, kb_id)
    if not kb:
        raise BizError(404, "知识库不存在")
    docs, _ = await kb_repo.list_documents(session, kb_id, 1, 100000)
    done = await chunk_repo.indexed_doc_ids(session, kb_id)
    todo = [d for d in docs if d.id not in done]
    stats = await _index_documents(session, owner_id, kb, todo, req.strategy, req.size, req.overlap)
    await session.commit()
    return {"kb_id": kb_id, "pending_docs": len(todo), **stats}


async def update_strategy(session: AsyncSession, owner_id: int, kb_id: int, req: ChunkRequest) -> dict:
    """更新切片策略（M3.2）：存于 kb.extra.chunk_strategy。"""
    kb = await kb_repo.get_kb(session, owner_id, kb_id)
    if not kb:
        raise BizError(404, "知识库不存在")
    kb.extra = {**(kb.extra or {}), "chunk_strategy": {
        "strategy": req.strategy, "size": req.size, "overlap": req.overlap,
    }}
    await session.commit()
    await session.refresh(kb)
    return {"kb_id": kb_id, "chunk_strategy": kb.extra.get("chunk_strategy")}


def _to_view(r: Chunk) -> dict:
    """切片列表视图（补知识库名与章节标题）。"""
    meta = r.meta or {}
    return {
        "id": r.id, "kb_id": r.kb_id, "chapter_id": r.chapter_id,
        "content": r.content, "word_count": r.word_count, "disabled": r.disabled,
        "kb_name": "", "chapter": meta.get("chapter_title", ""), "chars": r.word_count,
    }


def _to_detail(r: Chunk) -> dict:
    """切片详情视图（含来源元信息）。"""
    meta = r.meta or {}
    return {
        "id": r.id, "kb_id": r.kb_id, "chapter_id": r.chapter_id,
        "content": r.content, "word_count": r.word_count, "disabled": r.disabled,
        "meta": meta, "chapter": meta.get("chapter_title", ""),
        "chars": r.word_count, "char_start": meta.get("char_start"),
        "char_end": meta.get("char_end"),
    }


async def list_chunks(session, owner_id, kb_id=None, chapter_id=None, disabled=None, page=1, size=20):
    """切片列表（M4.1）：分页 + 过滤 + 补 kb 名。"""
    rows, total = await chunk_repo.list_chunks(session, owner_id, kb_id, chapter_id, disabled, page, size)
    views = []
    for r in rows:
        v = _to_view(r)
        if r.kb_id:
            kb = await kb_repo.get_kb(session, owner_id, r.kb_id)
            v["kb_name"] = kb.name if kb else ""
        views.append(v)
    return paginate(views, total, page, size)


async def get_chunk_detail(session, owner_id, chunk_id):
    """切片详情（M4.2）。"""
    row = await chunk_repo.get_chunk(session, owner_id, chunk_id)
    if not row:
        raise BizError(404, "切片不存在")
    return _to_detail(row)


async def get_chunk_source(session, owner_id, chunk_id):
    """来源定位（M4.3）：章节 + 字符区间。"""
    row = await chunk_repo.get_chunk(session, owner_id, chunk_id)
    if not row:
        raise BizError(404, "切片不存在")
    meta = row.meta or {}
    return {
        "id": row.id, "kb_id": row.kb_id, "chapter_id": row.chapter_id,
        "chapter_title": meta.get("chapter_title", ""),
        "char_start": meta.get("char_start"), "char_end": meta.get("char_end"),
        "word_count": row.word_count,
    }


async def search_chunks(session, owner_id, q, page=1, size=20):
    """关键词检索（M4.4）。"""
    rows, total = await chunk_repo.search_chunks(session, owner_id, q, page, size)
    return paginate([_to_view(r) for r in rows], total, page, size)


async def set_disabled(session, owner_id, chunk_id, disabled):
    """屏蔽/启用（M4.7）。"""
    row = await chunk_repo.set_disabled(session, owner_id, chunk_id, disabled)
    if not row:
        raise BizError(404, "切片不存在")
    await session.commit()
    return {"id": row.id, "disabled": row.disabled}


async def update_chunk_content(session, owner_id, chunk_id, content):
    """编辑内容并触发重向量化（M4.6）。"""
    row = await chunk_repo.update_content(session, owner_id, chunk_id, content)
    if not row:
        raise BizError(404, "切片不存在")
    adapter, _ = await _get_embed_adapter(session, owner_id)
    vec = (await adapter.embed([content]))[0]
    row.embedding = vec
    await session.flush()
    store = get_vector_store()
    await store.upsert(
        collection=f"kb_{row.kb_id}", ids=[row.id], vectors=[vec],
        documents=[content], metadatas=[{"owner_id": owner_id, "dim": len(vec)}],
    )
    await session.commit()
    return {"id": row.id, "word_count": row.word_count, "reembed": True}


async def similar_chunks(session, owner_id, chunk_id, top_k=5):
    """相似切片（M4.5）：以本切片向量查 Chroma top_k，排除自身。"""
    row = await chunk_repo.get_chunk(session, owner_id, chunk_id)
    if not row:
        raise BizError(404, "切片不存在")
    if not row.embedding:
        return []
    store = get_vector_store()
    hits = await store.query(
        collection=f"kb_{row.kb_id}", vector=row.embedding,
        top_k=top_k + 1, where={"dim": len(row.embedding)},
    )
    ids = [cid for cid, _ in hits if cid != row.id][:top_k]
    if not ids:
        return []
    dist_map = {cid: d for cid, d in hits}
    rows = (await session.execute(select(Chunk).where(Chunk.id.in_(ids)))).scalars().all()
    rows.sort(key=lambda r: dist_map[r.id])
    return [{"id": r.id, "content": r.content, "distance": dist_map[r.id]} for r in rows]
