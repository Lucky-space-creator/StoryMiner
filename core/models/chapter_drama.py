"""
章节漫剧 ORM 模型（M15 章节漫剧列）

整体思路：
    映射 story_chapter_drama 表，记录用户为某部小说选定的「章节漫剧片段」：
    取连续章节范围 [start_chapter, end_chapter]（最多 5 章），并解析出该范围内出场的角色，
    作为下游 AI 漫剧生产的素材单元。

关键点：
    1. 表名严格为 story_chapter_drama。
    2. novel_id 归属小说；owner_id 用户隔离。
    3. chapter_from / chapter_to 为章节序号（chapter_no），约束 end >= start 且跨度 <= 5。
    4. character_ids 为 JSONB 数组，存 story_character.id 列表（出场角色）。
    5. 逻辑删除用 deleted_at。

实现逻辑：
    声明式映射；时间字段服务端默认 now()。
"""
from datetime import datetime

from sqlalchemy import BigInteger, String, Integer, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class ChapterDrama(Base):
    """章节漫剧：选定小说连续章节范围（≤5 章）及出场角色的素材单元。"""

    __tablename__ = "story_chapter_drama"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    chapter_from: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_to: Mapped[int] = mapped_column(Integer, nullable=False)
    character_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    summary: Mapped[str | None] = mapped_column(String(1024))
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
