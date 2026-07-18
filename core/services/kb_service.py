"""
知识库业务逻辑（M2）

整体思路：
    聚合知识库 CRUD、文档追加/列表/删除、统计与导出，统一返回契约友好的 dict。

关键点：
    1. 所有操作按 owner_id 隔离，逻辑删除过滤；建库前校验小说归属与库名唯一。
    2. 追加文档复用 parse_service 上传解析链路，指定目标 kb_id。
    3. 删除库级联清理切片与文档；导出支持 JSON/CSV。

实现逻辑：
    委托 kb_repo / novel_repo / parse_service 完成数据访问与上传；本层只做业务编排与字段映射。
"""
import csv
import io
from datetime import datetime

import logging
from fastapi import UploadFile, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.knowledge_base import KBCreate, KBUpdate
from models.novel_content import KnowledgeBase, KbDocument
from models.chunk import Chunk
from repositories import kb_repo, novel_repo
from services import parse_service
from storage import delete as storage_delete
from vectorstore.factory import get_vector_store
from common.exceptions import BizError

logger = logging.getLogger(__name__)


def _iso(dt) -> str | None:
    """时间转 ISO 字符串。"""
    return dt.isoformat() if isinstance(dt, datetime) else None


def kb_out(kb: KnowledgeBase) -> dict:
    """知识库出参。"""
    return {
        "id": kb.id, "novel_id": kb.novel_id, "owner_id": kb.owner_id,
        "name": kb.name, "description": kb.description, "scope": kb.scope,
        "config": kb.config or {},
        "embedding_model": (kb.config or {}).get("embedding_model"),
        "created_at": _iso(kb.created_at), "updated_at": _iso(kb.updated_at),
    }


def doc_out(d) -> dict:
    """文档出参。"""
    return {
        "id": d.id, "kb_id": d.kb_id, "name": d.name, "doc_type": d.doc_type,
        "status": d.status, "word_count": d.word_count, "created_at": _iso(d.created_at),
    }


async def create_kb(session: AsyncSession, owner_id: int, data: KBCreate) -> dict:
    """建库（M2.1）：校验小说归属与库名唯一。"""
    if not await novel_repo.get_novel(session, owner_id, data.novel_id):
        raise BizError(404, "小说不存在")
    if await kb_repo.exists_name(session, data.novel_id, data.name):
        raise BizError(409, "同一小说下已存在同名知识库")
    kb = KnowledgeBase(
        novel_id=data.novel_id, owner_id=owner_id, name=data.name,
        scope=data.scope, description=data.description, config=data.config or {},
    )
    await kb_repo.create_kb(session, kb)
    await session.commit()
    await session.refresh(kb)
    return kb_out(kb)


async def list_kbs(session: AsyncSession, owner_id: int, novel_id: int | None, page: int, size: int):
    """知识库列表（分页，可按 novel_id 过滤）。"""
    items, total = await kb_repo.list_kbs(session, owner_id, novel_id, page, size)
    return [kb_out(k) for k in items], total


async def get_kb(session: AsyncSession, owner_id: int, kb_id: int) -> dict:
    """知识库详情（M2.3）。"""
    kb = await kb_repo.get_kb(session, owner_id, kb_id)
    if not kb:
        raise BizError(404, "知识库不存在")
    return kb_out(kb)


async def update_kb(session: AsyncSession, owner_id: int, kb_id: int, data: KBUpdate) -> dict:
    """更新知识库（M2.4）：改名需再次校验唯一。"""
    kb = await kb_repo.get_kb(session, owner_id, kb_id)
    if not kb:
        raise BizError(404, "知识库不存在")
    if data.name and data.name != kb.name and await kb_repo.exists_name(session, kb.novel_id, data.name):
        raise BizError(409, "同一小说下已存在同名知识库")
    for f in ("name", "scope", "description", "config"):
        v = getattr(data, f)
        if v is not None:
            setattr(kb, f, v)
    await session.commit()
    await session.refresh(kb)
    return kb_out(kb)


async def delete_kb(session: AsyncSession, owner_id: int, kb_id: int) -> None:
    """删除知识库（物理删除）：级联清理切片与链接，并删除向量集合。文档归小说所有，不删除。"""
    kb = await kb_repo.get_kb(session, owner_id, kb_id)
    if not kb:
        raise BizError(404, "知识库不存在")
    await kb_repo.cascade_delete(session, kb_id)
    await kb_repo.hard_delete_kb(session, kb_id)
    await session.commit()
    # 清理该知识库的向量集合（文档切片向量）
    store = get_vector_store()
    try:
        await store.delete_collection(f"kb_{kb_id}")
    except Exception as e:
        logger.warning("删除知识库清理 Chroma 集合失败 kb_%s: %s", kb_id, e)


async def append_document(
    session: AsyncSession, owner_id: int, kb_id: int,
    file: UploadFile, background: BackgroundTasks,
) -> dict:
    """追加文档（M2.6）：上传文件（归属小说，kb_id 置空）并触发解析，随后纳入该知识库。"""
    kb = await kb_repo.get_kb(session, owner_id, kb_id)
    if not kb:
        raise BizError(404, "知识库不存在")
    # 方案A：先按小说上传（不绑库），再建立链接关系
    result = await parse_service.handle_upload(session, owner_id, kb.novel_id, file, background)
    await kb_repo.add_kb_document(session, kb_id, result["doc_id"], owner_id)
    await session.commit()
    return result


async def list_documents(session: AsyncSession, owner_id: int, kb_id: int, page: int, size: int):
    """知识库文档列表（M2.7，基于链接表）。"""
    if not await kb_repo.get_kb(session, owner_id, kb_id):
        raise BizError(404, "知识库不存在")
    items, total = await kb_repo.list_documents(session, kb_id, page, size)
    return [doc_out(d) for d in items], total


async def add_document_to_kb(session: AsyncSession, owner_id: int, kb_id: int, doc_id: int) -> dict:
    """构建前置：校验文档归属并将文档纳入知识库（建立链接）。"""
    kb = await kb_repo.get_kb(session, owner_id, kb_id)
    if not kb:
        raise BizError(404, "知识库不存在")
    doc = await novel_repo.get_document(session, doc_id)
    if not doc or doc.owner_id != owner_id or doc.novel_id != kb.novel_id or doc.deleted_at is not None:
        raise BizError(400, "文档不存在或不属该小说")
    await kb_repo.add_kb_document(session, kb_id, doc_id, owner_id)
    await session.commit()
    return {"kb_id": kb_id, "doc_id": doc_id}


async def ensure_docs_in_kb(session: AsyncSession, owner_id: int, novel_id: int, kb_id: int) -> int:
    """自动将小说下所有已解析完成（status=done）的文档纳入知识库（幂等，已纳入则跳过）。

    整体思路：
        解决「构建索引」和「重建索引」路由未自动纳管文档导致构建成功但切片为 0 的问题。
        查小说下全部 done 文档 → 逐条建立 kb-doc 链接（已存在则跳过）。
    关键点：
        1. 只纳入已解析完成（done）的文档，pending/failed 的不纳入。
        2. 使用 add_kb_document 的幂等逻辑（已存在则跳过）。
        3. 返回本次新增的链接数，供日志记录。
    实现逻辑：
        novel_repo.list_documents_by_novel 取全部文档 → 过滤 done → 循环 add_kb_document。
    """
    kb = await kb_repo.get_kb(session, owner_id, kb_id)
    if not kb:
        raise BizError(404, "知识库不存在")
    docs, _ = await novel_repo.list_documents_by_novel(session, novel_id, 1, 100000)
    done_docs = [d for d in docs if d.status == "done"]
    added = 0
    for doc in done_docs:
        existing = (await session.execute(
            select(KbDocument).where(KbDocument.kb_id == kb_id, KbDocument.doc_id == doc.id)
        )).scalars().first()
        if not existing:
            session.add(KbDocument(kb_id=kb_id, doc_id=doc.id, owner_id=owner_id))
            added += 1
    if added > 0:
        logger.info("知识库 %s 自动纳管 %s 篇文档", kb_id, added)
    return added


async def delete_document(session: AsyncSession, owner_id: int, doc_id: int) -> None:
    """删除文档（物理删除）：清其切片与向量、MinIO 文件（跨所有知识库）。"""
    doc = await novel_repo.get_document(session, doc_id)
    if not doc or doc.owner_id != owner_id:
        raise BizError(404, "文档不存在")
    # 删除前收集切片与所属知识库，用于清理向量库
    rows = (await session.execute(
        select(Chunk.id, Chunk.kb_id).where(Chunk.doc_id == doc_id)
    )).all()
    object_key = doc.object_key
    await kb_repo.hard_delete_document(session, doc_id)
    await session.commit()
    # 清理 MinIO 文件
    if object_key:
        try:
            await storage_delete(object_key)
            print(f"MinIO对应的文档{doc.name}已清除")
        except Exception:
            pass
    # 清理 Chroma 中该文档在各知识库的向量
    store = get_vector_store()
    by_kb: dict[str, list[int]] = {}
    for cid, kb_id in rows:
        by_kb.setdefault(f"kb_{kb_id}", []).append(cid)
    for coll, ids in by_kb.items():
        try:
            await store.delete(collection=coll, ids=ids)
        except Exception as e:
            logger.warning("删除文档清理 Chroma 向量失败 %s: %s", coll, e)


async def stats(session: AsyncSession, owner_id: int, kb_id: int) -> dict:
    """知识库统计（M2.9）：文档数/切片数/字符量/更新时间。"""
    kb = await kb_repo.get_kb(session, owner_id, kb_id)
    if not kb:
        raise BizError(404, "知识库不存在")
    return {
        "kb_id": kb_id,
        "doc_count": await kb_repo.count_documents(session, kb_id),
        "chunk_count": await kb_repo.count_chunks(session, kb_id),
        "chars": await kb_repo.sum_word_count(session, kb_id),
        "updated_at": _iso(kb.updated_at),
    }


async def export(session: AsyncSession, owner_id: int, kb_id: int, fmt: str = "json"):
    """导出配置+切片（M2.10）：返回 (内容, media_type, 文件名)。"""
    kb = await kb_repo.get_kb(session, owner_id, kb_id)
    if not kb:
        raise BizError(404, "知识库不存在")
    chunks = await kb_repo.list_chunks(session, kb_id)
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["chunk_id", "doc_id", "chapter_id", "idx", "word_count", "content"])
        for c in chunks:
            writer.writerow([c.id, c.doc_id, c.chapter_id, c.idx, c.word_count, c.content])
        return buf.getvalue(), "text/csv", f"kb_{kb_id}_chunks.csv"
    data = {
        "knowledge_base": kb_out(kb),
        "chunks": [
            {"id": c.id, "doc_id": c.doc_id, "chapter_id": c.chapter_id,
             "idx": c.idx, "content": c.content, "word_count": c.word_count, "meta": c.meta}
            for c in chunks
        ],
    }
    return data, "application/json", f"kb_{kb_id}_export.json"
