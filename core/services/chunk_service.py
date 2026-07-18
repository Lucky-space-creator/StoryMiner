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
import asyncio
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from models.chunk import Chunk
from schemas.chunk import ChunkRequest
from repositories import kb_repo, chunk_repo, llm_repo, novel_repo
from services import chunker, llm_adapters, task_service
from common import crypto
from common.exceptions import BizError
from common.response import paginate
from common.threadpool import run_in_thread
from storage import read as read_file, exists as exists_file
from vectorstore.factory import get_vector_store
from db import SessionLocal
from config import BG_TASK_TIMEOUT, EMBED_BATCH, EMBED_CONCURRENCY, CHROMA_UPSERT_BATCH

# 切割进度统一写入 story_async_task（task_id 即 AsyncTask.id），由前端全局轮询读取。


async def _get_embed_adapter(session: AsyncSession, owner_id: int, embed_config_id: int | None = None):
    """取嵌入模型适配器：优先用指定的 embed 配置，否则走默认分发链。返回 (adapter, model_name)。"""
    if embed_config_id:
        cfg = await llm_repo.get_visible(session, owner_id, embed_config_id)
        if cfg and cfg.llm_type == "embed" and cfg.status == "active" and cfg.api_key:
            return llm_adapters.get_adapter(cfg, crypto.decrypt(cfg.api_key)), cfg.model
        # 指定的配置无效（不存在/非 embed/未启用/无密钥），回退默认链
    cfgs = await llm_repo.list_for_dispatch(session, owner_id, "embed")
    if not cfgs:
        raise BizError(400, "尚未配置嵌入模型（llm_type=embed），请先在模型管理中添加")
    cfg = cfgs[0]
    return llm_adapters.get_adapter(cfg, crypto.decrypt(cfg.api_key)), cfg.model


async def _index_documents(
    session: AsyncSession, owner_id: int, kb, docs: list, strategy: str, size: int, overlap: int,
    task_id: str | None = None, embed_config_id: int | None = None,
) -> dict:
    """对给定文档执行切章→切片→嵌入→落库（Chroma-only，流式分批以控内存），返回统计。

    整体思路：先切章切片得到纯文本片段列表，再按批次并发嵌入、分批落库 PG 与 Chroma，
        全程不长期驻留全量向量/ORM 对象，支撑十万~千万字文档而不过载。
    关键点：
        1. 嵌入并发受 EMBED_CONCURRENCY 信号量限制，避免打爆本地 Ollama/远端 API。
        2. 每 CHROMA_UPSERT_BATCH 条切片即 bulk_insert + Chroma upsert 一批并释放内存（expunge）。
        3. 向量仅存 Chroma，PG 的 story_chunk 不再保存 embedding 列（Chroma-only）。
    实现逻辑：
        切章切片 → 滑动窗口并发嵌入 → 分批组装 Chunk + upsert → 回写进度 → 释放大对象。
    """
    pieces: list[tuple] = []  # (doc_id, piece)
    skipped_no_key = 0  # 统计 object_key 为空的文档数
    if task_id:
        await task_service.update_task_progress(task_id, progress=5, stage="chunking", status="running")
    for doc in docs:
        if not doc.object_key or not await exists_file(doc.object_key):
            skipped_no_key += 1
            continue
        # 文件读取为同步阻塞 IO，放线程池避免阻塞事件循环影响其他用户请求
        raw = await run_in_thread(read_file, doc.object_key)
        text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
        for p in chunker.chunk_document(text, strategy, size, overlap):
            pieces.append((doc.id, p))
    # 区分：docs 本身为空（知识库未纳管文档） vs 文档存在但文件不可读
    if not pieces:
        if task_id:
            if docs and skipped_no_key == len(docs):
                await task_service.update_task_progress(
                    task_id, progress=100, stage="done", status="failed",
                    message=f"所有文档文件丢失（{len(docs)} 篇），请重新上传",
                    chunk_count=0, doc_count=0, model=None, dim=0,
                )
            elif not docs:
                await task_service.update_task_progress(
                    task_id, progress=100, stage="done", status="failed",
                    message="知识库未关联任何文档，请先添加文档",
                    chunk_count=0, doc_count=0, model=None, dim=0,
                )
            else:
                await task_service.update_task_progress(
                    task_id, progress=100, stage="done", status="failed",
                    message="无需切割（无有效文档内容）",
                    chunk_count=0, doc_count=0, model=None, dim=0,
                )
        if docs and skipped_no_key == len(docs):
            raise BizError(400, f"所有文档文件丢失（{len(docs)} 篇），请重新上传文档")
        if not docs:
            raise BizError(400, "知识库未关联任何文档，请先通过「构建索引-选择文档」将文档加入知识库")
        raise BizError(400, "文档无有效内容，无法切割")
    # 取嵌入适配器（批量嵌入：OpenAI 原生批量，Ollama 已改 /api/embed 批量）
    if task_id:
        await task_service.update_task_progress(task_id, progress=20, stage="embedding", status="running")
    adapter, model = await _get_embed_adapter(session, owner_id, embed_config_id)
    total = len(pieces)
    texts = [p[1]["content"] for p in pieces]
    dim = 0
    seq = 0       # KB 内切片序号（按片段顺序递增）
    done = 0      # 已嵌入切片数（进度用）
    chunks_buffer: list[Chunk] = []
    vectors_buffer: list[list[float]] = []
    metas_buffer: list[dict] = []
    store = get_vector_store()

    async def _flush() -> None:
        """将缓冲区的切片批量落库 PG + Chroma，并释放内存（expunge）。"""
        nonlocal done
        if not chunks_buffer:
            return
        await chunk_repo.bulk_insert(session, chunks_buffer)
        await session.flush()  # 落库并拿到自增 id，供 Chroma upsert 关联
        await store.upsert(
            collection=f"kb_{kb.id}",
            ids=[c.id for c in chunks_buffer],
            vectors=vectors_buffer,
            documents=[c.content for c in chunks_buffer],
            metadatas=metas_buffer,
        )
        for c in chunks_buffer:
            session.expunge(c)  # 释放 ORM 身份映射占用，避免大文档内存堆积
        chunks_buffer.clear()
        vectors_buffer.clear()
        metas_buffer.clear()
        if task_id:
            prog = min(95, 20 + int(70 * done / total))
            await task_service.update_task_progress(task_id, progress=prog, stage="embedding", status="running")

    # 并发嵌入（滑动窗口）：最多 EMBED_CONCURRENCY 个批次在途，按片段顺序落库以释放内存
    sem = asyncio.Semaphore(EMBED_CONCURRENCY)
    in_flight: dict[int, asyncio.Future] = {}
    next_start = 0

    def _launch(start: int) -> None:
        batch = texts[start:start + EMBED_BATCH]
        async def _run() -> list[list[float]]:
            async with sem:
                return await adapter.embed(batch)
        in_flight[start] = asyncio.ensure_future(_run())

    while next_start < total or in_flight:
        while len(in_flight) < EMBED_CONCURRENCY and next_start < total:
            _launch(next_start)
            next_start += EMBED_BATCH
        start = min(in_flight)  # 取最早批次，保证按片段顺序落库
        vecs = await in_flight.pop(start)
        if not dim and vecs:
            dim = len(vecs[0])
        for (doc_id, p), vec in zip(pieces[start:start + len(vecs)], vecs):
            seq += 1
            chunks_buffer.append(Chunk(
                doc_id=doc_id, novel_id=kb.novel_id, kb_id=kb.id, owner_id=owner_id,
                idx=seq,
                content=p["content"], word_count=len(p["content"]),
                meta={
                    "chapter_title": p["title"],
                    "char_start": p["char_start"],
                    "char_end": p["char_end"],
                    "dim": dim,
                },
            ))
            vectors_buffer.append(vec)
            metas_buffer.append({"owner_id": owner_id, "dim": dim})
        done += len(vecs)
        if len(chunks_buffer) >= CHROMA_UPSERT_BATCH:
            await _flush()
    await _flush()  # 收尾落库剩余切片
    if task_id:
        await task_service.update_task_progress(task_id, progress=95, stage="storing", status="running")
    # 记录本次实际使用的嵌入模型，供知识库详情展示（避免展示虚假模型名）
    kb.config = {**(kb.config or {}), "embedding_model": model}
    # 释放大对象引用（texts/pieces 仅文本，量可控；此处显式释放便于 GC）
    del pieces, texts
    return {"chunk_count": seq, "doc_count": len(docs), "chars": sum((d.word_count or 0) for d in docs), "model": model, "dim": dim}


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
    stats = await _index_documents(session, owner_id, kb, docs, req.strategy, req.size, req.overlap, embed_config_id=req.embed_config_id)
    await session.commit()
    return {"kb_id": kb_id, **stats, "full": full}


async def chunk_and_index_async(owner_id: int, kb_id: int, req: ChunkRequest, full: bool, task_id: int) -> None:
    """后台切割+向量化（M3.1）：独立会话执行，按 batch 回写进度到统一任务表。"""
    try:
        await task_service.update_task_progress(task_id, progress=0, stage="preparing", status="running", started_at=datetime.now(timezone.utc))
        async with SessionLocal() as session:
            kb = await kb_repo.get_kb(session, owner_id, kb_id)
            if not kb:
                raise BizError(404, "知识库不存在")
            docs, _ = await kb_repo.list_documents(session, kb_id, 1, 100000)
            if full:
                # 全量重建前校验：若无文档可切，则不清空旧数据（避免旧切片被删但新切片没写入）
                if not docs:
                    await task_service.update_task_progress(
                        task_id, progress=100, stage="done", status="failed",
                        message="知识库未关联任何文档，请先添加文档后再构建索引",
                        chunk_count=0, doc_count=0, model=None, dim=0,
                    )
                    return
                await chunk_repo.delete_by_kb(session, kb_id)
                # 清空向量库集合，避免重建后残留旧 chunk 向量
                store = get_vector_store()
                await store.delete_collection(f"kb_{kb_id}")
            # 超时兜底：避免超大文档嵌入卡死导致前端轮询永久挂起（BG_TASK_TIMEOUT=0 表示不限）
            timeout = BG_TASK_TIMEOUT if BG_TASK_TIMEOUT and BG_TASK_TIMEOUT > 0 else None
            stats = await asyncio.wait_for(
                _index_documents(session, owner_id, kb, docs, req.strategy, req.size, req.overlap, task_id=task_id, embed_config_id=req.embed_config_id),
                timeout=timeout,
            )
            await session.commit()
        await task_service.update_task_progress(task_id, progress=100, stage="done", status="success", message="切割完成", finished_at=datetime.now(timezone.utc), **stats)
    except Exception as e:  # 后台任务异常：回写失败状态，避免前端轮询卡住
        from common.task_errors import to_user_error
        await task_service.update_task_progress(task_id, progress=0, stage="failed", status="failed", error=to_user_error(e), finished_at=datetime.now(timezone.utc))


async def build_doc_async(owner_id: int, kb_id: int, doc_id: int, req: ChunkRequest, task_id: int) -> None:
    """后台构建单文档（方案A 构建选文件）：替换该文档在知识库内的旧切片并重新切分向量化。"""
    try:
        await task_service.update_task_progress(task_id, progress=0, stage="preparing", status="running", started_at=datetime.now(timezone.utc))
        async with SessionLocal() as session:
            kb = await kb_repo.get_kb(session, owner_id, kb_id)
            if not kb:
                raise BizError(404, "知识库不存在")
            doc = await novel_repo.get_document(session, doc_id)
            if not doc or doc.owner_id != owner_id or doc.novel_id != kb.novel_id or doc.deleted_at is not None:
                raise BizError(400, "文档不存在或不属该小说")
            # 确保文档已纳入该知识库（建立链接）
            await kb_repo.add_kb_document(session, kb_id, doc_id, owner_id)
            # 清除该文档在知识库内的旧切片与向量，避免重建后残留
            old = (await session.execute(
                select(Chunk.id).where(Chunk.kb_id == kb_id, Chunk.doc_id == doc_id)
            )).scalars().all()
            if old:
                await session.execute(delete(Chunk).where(Chunk.kb_id == kb_id, Chunk.doc_id == doc_id))
                store = get_vector_store()
                try:
                    await store.delete(collection=f"kb_{kb_id}", ids=list(old))
                except Exception:
                    pass
            # 超时兜底：避免超大文档嵌入卡死导致前端轮询永久挂起（BG_TASK_TIMEOUT=0 表示不限）
            timeout = BG_TASK_TIMEOUT if BG_TASK_TIMEOUT and BG_TASK_TIMEOUT > 0 else None
            stats = await asyncio.wait_for(
                _index_documents(session, owner_id, kb, [doc], req.strategy, req.size, req.overlap, task_id=task_id, embed_config_id=req.embed_config_id),
                timeout=timeout,
            )
            await session.commit()
        await task_service.update_task_progress(task_id, progress=100, stage="done", status="success", message="切割完成", finished_at=datetime.now(timezone.utc), **stats)
    except Exception as e:  # 后台任务异常：回写失败状态，避免前端轮询卡住
        from common.task_errors import to_user_error
        await task_service.update_task_progress(task_id, progress=0, stage="failed", status="failed", error=to_user_error(e), finished_at=datetime.now(timezone.utc))


async def incremental_index(session: AsyncSession, owner_id: int, kb_id: int, req: ChunkRequest) -> dict:
    """仅增量索引新增/未索引文档（M3.4）。"""
    kb = await kb_repo.get_kb(session, owner_id, kb_id)
    if not kb:
        raise BizError(404, "知识库不存在")
    docs, _ = await kb_repo.list_documents(session, kb_id, 1, 100000)
    done = await chunk_repo.indexed_doc_ids(session, kb_id)
    todo = [d for d in docs if d.id not in done]
    stats = await _index_documents(session, owner_id, kb, todo, req.strategy, req.size, req.overlap, embed_config_id=req.embed_config_id)
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
    """编辑内容并触发重向量化（M4.6）。

    整体思路：先重向量化再写回，避免向量化失败时内容已改而向量未更新。
    关键点：embed 可能因网络/密钥异常失败，需捕获并转为业务错误而非 500。
    实现逻辑：取适配器 → 重向量化（容错）→ 更新内容与向量 → 同步向量库。
    """
    adapter, _ = await _get_embed_adapter(session, owner_id)
    try:
        vec = (await adapter.embed([content]))[0]
    except Exception as e:  # noqa: BLE001 重向量化失败需友好提示而非 500
        raise BizError(400, f"重向量化失败：{e}")
    row = await chunk_repo.update_content(session, owner_id, chunk_id, content)
    if not row:
        raise BizError(404, "切片不存在")
    # Chroma-only：向量仅存 Chroma，PG 不再保存 embedding 列，仅同步向量库。
    await session.flush()
    store = get_vector_store()
    await store.upsert(
        collection=f"kb_{row.kb_id}", ids=[row.id], vectors=[vec],
        documents=[content], metadatas=[{"owner_id": owner_id, "dim": len(vec)}],
    )
    await session.commit()
    return {"id": row.id, "word_count": row.word_count, "reembed": True}


async def similar_chunks(session, owner_id, chunk_id, top_k=5):
    """相似切片（M4.5）：以本切片内容重嵌入得到向量查 Chroma top_k，排除自身。

    整体思路：向量以 Chroma 为唯一来源，PG 不再存 embedding 列，故用内容重新嵌入取向量。
    关键点：仅单条切片重嵌入，开销极小；失败转业务错误而非 500。
    实现逻辑：取适配器 → 重嵌入内容 → 查 Chroma → 回 PG 组装业务字段。
    """
    row = await chunk_repo.get_chunk(session, owner_id, chunk_id)
    if not row:
        raise BizError(404, "切片不存在")
    adapter, _ = await _get_embed_adapter(session, owner_id)
    try:
        vec = (await adapter.embed([row.content]))[0]
    except Exception as e:  # noqa: BLE001 重嵌入失败需友好提示而非 500
        raise BizError(400, f"相似切片查询失败：{e}")
    store = get_vector_store()
    hits = await store.query(
        collection=f"kb_{row.kb_id}", vector=vec,
        top_k=top_k + 1, where={"dim": len(vec)},
    )
    ids = [cid for cid, _ in hits if cid != row.id][:top_k]
    if not ids:
        return []
    dist_map = {cid: d for cid, d in hits}
    rows = (await session.execute(select(Chunk).where(Chunk.id.in_(ids)))).scalars().all()
    rows.sort(key=lambda r: dist_map[r.id])
    return [{"id": r.id, "content": r.content, "distance": dist_map[r.id]} for r in rows]
