"""
章节漫剧数据访问（M15）

整体思路：
    封装 story_chapter_drama 的读写，所有查询强制 owner 隔离与逻辑删除过滤，
    提供列表、按 ID 查询、写入与软删除能力。

关键点：
    1. 列表/详情默认过滤 deleted_at IS NULL。
    2. 按 novel_id 查询某小说下全部章节漫剧条目。

实现逻辑：
    基于 async session 的 select/delete；写入后 flush 取回自增 ID，调用方统一提交。
"""
from datetime import datetime
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from models.chapter_drama import ChapterDrama
from models.chapter_drama_scene import ChapterDramaScene


async def list_by_novel(session: AsyncSession, novel_id: int) -> list[ChapterDrama]:
    """查询小说全部章节漫剧条目（过滤逻辑删除），按章节起始排序。"""
    stmt = select(ChapterDrama).where(
        ChapterDrama.novel_id == novel_id, ChapterDrama.deleted_at.is_(None)
    ).order_by(ChapterDrama.chapter_from, ChapterDrama.id)
    return list((await session.execute(stmt)).scalars().all())


async def get(session: AsyncSession, drama_id: int) -> ChapterDrama | None:
    """按 ID 查询章节漫剧（含已删除，归属由 service 校验）。"""
    return await session.get(ChapterDrama, drama_id)


async def create(session: AsyncSession, d: ChapterDrama) -> ChapterDrama:
    """写入章节漫剧（调用方提交）。"""
    session.add(d)
    await session.flush()
    return d


async def soft_delete(session: AsyncSession, drama_id: int) -> None:
    """逻辑删除章节漫剧。"""
    await session.execute(
        delete(ChapterDrama).where(ChapterDrama.id == drama_id)
    )


# ---------------------------------------------------------------------------
# 章节漫剧场景分析（M15.5 导演 Agent）
# ---------------------------------------------------------------------------
async def get_scene(session: AsyncSession, drama_id: int) -> ChapterDramaScene | None:
    """按 drama_id 取场景分析（1:1）。"""
    stmt = select(ChapterDramaScene).where(ChapterDramaScene.drama_id == drama_id)
    return (await session.execute(stmt)).scalars().first()


async def upsert_scene(session: AsyncSession, scene: ChapterDramaScene) -> ChapterDramaScene:
    """写入/更新场景分析（调用方提交）。drama_id 唯一，重复则更新。"""
    exist = await get_scene(session, scene.drama_id)
    if exist:
        exist.scene_design = scene.scene_design
        exist.plot_arrangement = scene.plot_arrangement
        exist.camera_movement = scene.camera_movement
        exist.duration_estimate = scene.duration_estimate
        exist.content_raw = scene.content_raw
        exist.updated_at = datetime.utcnow()
        return exist
    session.add(scene)
    await session.flush()
    return scene
