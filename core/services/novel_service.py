"""
小说业务逻辑（M1）

整体思路：
    聚合小说 CRUD、章节校正/拆分/合并、详情统计，统一返回契约友好的 dict。

关键点：
    1. 所有查询按 owner_id 隔离，逻辑删除过滤。
    2. 详情附带章节数统计。
    3. 拆分/合并操作维护章节内容与编号。

实现逻辑：
    委托 novel_repo 完成数据访问；本层只做业务编排与字段映射。
"""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from schemas.novel import NovelCreate, NovelUpdate, ChapterCorrect, ChapterSplit, ChapterMerge
from models.novel_content import Novel, Chapter
from repositories import novel_repo
from common.exceptions import BizError


def _iso(dt) -> str | None:
    """时间转 ISO 字符串。"""
    return dt.isoformat() if isinstance(dt, datetime) else None


def novel_out(n: Novel, chapter_count: int = 0) -> dict:
    """小说出参。"""
    return {
        "id": n.id, "owner_id": n.owner_id, "name": n.name, "author": n.author,
        "summary": n.summary, "description": n.description, "cover": n.cover,
        "status": n.status, "tags": n.tags or [], "chapter_count": chapter_count,
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
    """删除小说（逻辑删除）。"""
    novel = await novel_repo.get_novel(session, owner_id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")
    await novel_repo.soft_delete_novel(session, novel)
    await session.commit()


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
    chapters.sort(key=lambda c: (c.chapter_no, c.id))
    first = chapters[0]
    merged = "\n\n".join(c.content for c in chapters)
    first.content = merged
    first.word_count = len(merged.strip())
    await novel_repo.soft_delete_chapters(session, [c.id for c in chapters[1:]])
    await session.commit()
    await session.refresh(first)
    return chapter_out(first)
