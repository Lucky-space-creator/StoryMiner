"""
小说业务逻辑（M1）

整体思路：
    聚合小说 CRUD、章节校正/拆分/合并、详情统计，统一返回契约友好的 dict。

关键点：
    1. 所有查询按 owner_id 隔离，逻辑删除过滤。
    2. 详情附带章节数统计。
    3. 拆分/合并操作维护章节内容与编号。
    4. AI 概括生成（generate_ai_summary）：解析完成后异步调用 LLM 生成 ≤200 字概括，
       含主人公与大体情节，回写 novel.ai_summary，不覆盖用户填写的 summary。

实现逻辑：
    委托 novel_repo 完成数据访问；本层只做业务编排与字段映射。
"""
import logging
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.novel import NovelCreate, NovelUpdate, ChapterCorrect, ChapterSplit, ChapterMerge
from models.novel_content import Novel, Chapter, Document, KnowledgeBase
from repositories import novel_repo, llm_repo
from services import llm_adapters, task_service
from storage import delete as storage_delete
from vectorstore.factory import get_vector_store
from common import crypto
from common.exceptions import BizError
from db import SessionLocal

logger = logging.getLogger(__name__)


# AI 概括正文拼接上限（避免超长上下文，按章节顺序取前 N 字）
_AI_SUMMARY_CHARS = 6000


def _iso(dt) -> str | None:
    """时间转 ISO 字符串。"""
    return dt.isoformat() if isinstance(dt, datetime) else None


def novel_out(n: Novel, chapter_count: int = 0) -> dict:
    """小说出参。"""
    return {
        "id": n.id, "owner_id": n.owner_id, "name": n.name, "author": n.author,
        "summary": n.summary, "ai_summary": n.ai_summary, "description": n.description,
        "cover": n.cover, "status": n.status, "tags": n.tags or [],
        "chapter_count": chapter_count,
        "created_at": _iso(n.created_at), "updated_at": _iso(n.updated_at),
    }


def chapter_out(c: Chapter) -> dict:
    """章节出参。"""
    return {
        "id": c.id, "novel_id": c.novel_id, "title": c.title, "volume": c.volume,
        "chapter_no": c.chapter_no, "word_count": c.word_count,
        "char_start": c.char_start, "char_end": c.char_end,
        "created_at": _iso(c.created_at),
    }


async def create_novel(session: AsyncSession, owner_id: int, data: NovelCreate) -> Novel:
    """创建小说空间（M1.1）。"""
    novel = Novel(
        owner_id=owner_id, name=data.name,
        author=data.author, summary=data.summary, tags=data.tags or [],
    )
    await novel_repo.create_novel(session, novel)
    await session.commit()
    await session.refresh(novel)
    return novel


async def list_novels(session: AsyncSession, owner_id: int, page: int, size: int):
    """小说列表（分页）。"""
    items, total = await novel_repo.list_novels(session, owner_id, page, size)
    return [novel_out(n) for n in items], total


async def get_novel(session: AsyncSession, owner_id: int, novel_id: int) -> dict:
    """小说详情 + 章节数（M1.5）。"""
    novel = await novel_repo.get_novel(session, owner_id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")
    cnt = await novel_repo.count_chapters(session, novel_id)
    return novel_out(novel, cnt)


async def update_novel(session: AsyncSession, owner_id: int, novel_id: int, data: NovelUpdate) -> dict:
    """更新元信息/封面（M1.6）。"""
    novel = await novel_repo.get_novel(session, owner_id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")
    for f in ("name", "author", "summary", "description", "cover", "status", "tags"):
        v = getattr(data, f)
        if v is not None:
            setattr(novel, f, v)
    await session.commit()
    await session.refresh(novel)
    return novel_out(novel)


async def delete_novel(session: AsyncSession, owner_id: int, novel_id: int) -> None:
    """删除小说（物理删除，级联清理文档/章节/知识库/切片/解析任务，并清理 MinIO 与向量）。"""
    novel = await novel_repo.get_novel(session, owner_id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")
    # 收集 MinIO 对象键（文档文件）
    doc_keys = (await session.execute(
        select(Document.object_key).where(Document.novel_id == novel_id)
    )).scalars().all()
    object_keys = [k for k in doc_keys if k]
    # 收集该小说下知识库 id（用于清理 Chroma 向量集合）
    kb_ids = (await session.execute(
        select(KnowledgeBase.id).where(
            KnowledgeBase.novel_id == novel_id, KnowledgeBase.owner_id == owner_id
        )
    )).scalars().all()
    # 物理删除全部从属数据
    await novel_repo.hard_delete_novel(session, novel_id)
    await session.commit()
    # 清理 MinIO 文件
    for k in object_keys:
        try:
            await storage_delete(k)
        except Exception:
            pass
    # 清理 Chroma 向量集合
    vstore = get_vector_store()
    for kb_id in kb_ids:
        try:
            await vstore.delete_collection(f"kb_{kb_id}")
        except Exception as e:
            logger.warning("删除小说清理 Chroma 集合失败 kb_%s: %s", kb_id, e)


async def list_chapters(session: AsyncSession, owner_id: int, novel_id: int, page: int = 1, size: int = 20, q: str | None = None) -> dict:
    """章节列表（M1.3）：分页 + 标题查询，返回 {list,total,page,size}。"""
    if not await novel_repo.get_novel(session, owner_id, novel_id):
        raise BizError(404, "小说不存在")
    items, total = await novel_repo.list_chapters(session, novel_id, page, size, q)
    return {"list": [chapter_out(c) for c in items], "total": total, "page": page, "size": size}


async def get_chapter_detail(session: AsyncSession, owner_id: int, chapter_id: int) -> dict:
    """章节详情（含正文，M1.3 查看）。"""
    ch = await novel_repo.get_chapter(session, chapter_id)
    if not ch or ch.owner_id != owner_id:
        raise BizError(404, "章节不存在")
    d = chapter_out(ch)
    d["content"] = ch.content
    return d


async def correct_chapter(session: AsyncSession, owner_id: int, chapter_id: int, data: ChapterCorrect) -> dict:
    """章节校正：重命名/内容编辑（M1.4）。"""
    ch = await novel_repo.get_chapter(session, chapter_id)
    if not ch or ch.owner_id != owner_id:
        raise BizError(404, "章节不存在")
    if data.title is not None:
        ch.title = data.title
    if data.content is not None:
        ch.content = data.content
        ch.word_count = len(data.content.strip())
    await session.commit()
    await session.refresh(ch)
    return chapter_out(ch)


async def split_chapter(session: AsyncSession, owner_id: int, chapter_id: int, data: ChapterSplit) -> list[dict]:
    """按字符偏移拆分章节（M1.4）。"""
    ch = await novel_repo.get_chapter(session, chapter_id)
    if not ch or ch.owner_id != owner_id:
        raise BizError(404, "章节不存在")
    content = ch.content
    off = data.offset
    if off <= 0 or off >= len(content):
        raise BizError(400, "拆分偏移超出章节内容范围")
    part1, part2 = content[:off], content[off:]
    ch.content = part1
    ch.word_count = len(part1.strip())
    new = Chapter(
        novel_id=ch.novel_id, owner_id=owner_id,
        title=data.title or f"{ch.title or ''}（续）",
        chapter_no=ch.chapter_no + 1, content=part2,
        word_count=len(part2.strip()),
    )
    await novel_repo.add_chapters(session, [new])
    # 后续章节 chapter_no 顺延（排除新插入的章节本身），避免拆分后编号重复导致顺序错乱
    await session.execute(
        update(Chapter).where(
            Chapter.novel_id == ch.novel_id,
            Chapter.chapter_no > ch.chapter_no,
            Chapter.id != new.id,
        ).values(chapter_no=Chapter.chapter_no + 1)
    )
    await session.commit()
    await session.refresh(ch)
    await session.refresh(new)
    return [chapter_out(ch), chapter_out(new)]


async def merge_chapters(session: AsyncSession, owner_id: int, chapter_ids: list[int]) -> dict:
    """合并多个章节（M1.4）：按章节号排序，保留首章，软删其余。"""
    if len(chapter_ids) < 2:
        raise BizError(400, "至少选择两个章节进行合并")
    chapters = []
    for cid in chapter_ids:
        c = await novel_repo.get_chapter(session, cid)
        if not c or c.owner_id != owner_id:
            raise BizError(404, "章节不存在或无权操作")
        chapters.append(c)
    if len({c.novel_id for c in chapters}) > 1:
        raise BizError(400, "不能跨小说合并章节")
    chapters.sort(key=lambda c: (c.chapter_no, c.id))
    first = chapters[0]
    merged = "\n\n".join(c.content for c in chapters)
    first.content = merged
    first.word_count = len(merged.strip())
    await novel_repo.hard_delete_chapters(session, [c.id for c in chapters[1:]])
    await session.commit()
    await session.refresh(first)
    return chapter_out(first)


# ---------------------------------------------------------------------------
# AI 概括生成（解析完成后的异步任务）
# ---------------------------------------------------------------------------
async def _collect_chapters_text_for_summary(session: AsyncSession, novel_id: int) -> str:
    """按章节顺序拼接正文，截断到 _AI_SUMMARY_CHARS 字以避免超长上下文。"""
    chapters: list[Chapter] = await novel_repo.list_all_chapters(session, novel_id)
    if not chapters:
        return ""
    parts, total = [], 0
    for c in chapters:
        seg = f"【{c.title or ('第' + str(c.chapter_no) + '章')}】\n{c.content}"
        if total + len(seg) > _AI_SUMMARY_CHARS:
            seg = seg[: max(0, _AI_SUMMARY_CHARS - total)]
            parts.append(seg)
            break
        parts.append(seg)
        total += len(seg)
    return "\n\n".join(parts)


async def generate_ai_summary(
    novel_id: int, owner_id: int, novel_name: str, async_task_id: int | None = None,
) -> str | None:
    """异步生成小说 AI 概括（≤200 字，含主人公与大体情节），回写 novel.ai_summary。

    整体思路：
        取已解析章节正文 → 调 chat 适配器一次性生成 → 回写 novel.ai_summary。
    关键点：
        1. 独立 SessionLocal，避免持有请求会话（与 _run_parse 同样的后台任务模式）。
        2. 无章节或无 chat 模型配置时静默失败，仅写错误到异步任务记录。
        3. 概括字数限制由 prompt 显式约束，回写前再做一次硬截断兜底。
    实现逻辑：
        读章节正文拼接 → 构造 system/user 消息 → adapter.chat → 截断 → 回写并更新任务进度。
    """
    async with SessionLocal() as session:
        novel = await novel_repo.get_novel(session, owner_id, novel_id)
        if not novel:
            logger.warning("AI 概括生成失败：小说 %s 不存在或无权限", novel_id)
            return None
        try:
            # 取 chat 模型适配器（降级分发链首个）
            cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
            if not cfgs:
                logger.warning("AI 概括生成跳过：用户 %s 未配置 chat 模型", owner_id)
                if async_task_id:
                    await task_service.update_task_progress(
                        async_task_id, stage="skipped", status="failed",
                        error="尚未配置对话模型（llm_type=chat），跳过 AI 概括生成",
                    )
                return None
            cfg = cfgs[0]
            adapter = llm_adapters.get_adapter(cfg, crypto.decrypt(cfg.api_key))

            text = await _collect_chapters_text_for_summary(session, novel_id)
            if not text:
                logger.info("AI 概括生成跳过：小说 %s 暂无章节正文", novel_id)
                if async_task_id:
                    await task_service.update_task_progress(
                        async_task_id, stage="skipped", status="failed",
                        error="暂无章节正文，跳过 AI 概括生成",
                    )
                return None

            if async_task_id:
                await task_service.update_task_progress(
                    async_task_id, stage="generating", progress=20, status="running",
                )

            system_prompt = (
                "你是一位资深的小说编辑，擅长用精炼的语言概括小说核心信息。"
                "请根据提供的小说正文，输出一份不超过 200 字的概括，"
                "内容需包含：1) 小说主人公是谁；2) 小说的总体情节走向。"
                "严格控制在 200 字以内，不要分点、不要换行，直接输出概括文本。"
            )
            user_prompt = f"小说名称：《{novel_name}》\n\n小说正文片段：\n{text}"

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            ai_text = await adapter.chat(messages, temperature=0.3, max_tokens=400)
            # 记录 Token 用量到仪表盘
            usage = adapter.get_last_usage()
            if usage and cfg.id:
                await task_service.record_llm_usage(
                    owner_id=owner_id, config_id=cfg.id, model=cfg.model,
                    task_type="summary", tokens_in=usage.get("tokens_in", 0),
                    tokens_out=usage.get("tokens_out", 0),
                )
            # 硬截断兜底，确保 ≤200 字
            ai_summary = (ai_text or "").strip()[:200]

            novel.ai_summary = ai_summary
            await session.commit()

            if async_task_id:
                await task_service.update_task_progress(
                    async_task_id, stage="done", progress=100, status="success",
                )
            logger.info("AI 概括生成成功：小说 %s", novel_id)
            return ai_summary
        except Exception as e:
            from common.task_errors import to_user_error
            await session.rollback()
            logger.exception("AI 概括生成失败：小说 %s，错误：%s", novel_id, e)
            if async_task_id:
                await task_service.update_task_progress(
                    async_task_id, stage="failed", status="failed", error=to_user_error(e),
                )
            return None

