"""
章节漫剧场景分析 ORM（M15.5 导演 Agent）

整体思路：
    映射 story_chapter_drama_scene 表，记录「导演 Agent」为某个章节漫剧生成的场景分析：
    场景设计 / 剧情安排 / 镜头运转 / 预计时长 四个列表字段，供前端详情页以列表展示与保存。

关键点：
    1. 表名严格为 story_chapter_drama_scene，drama_id 唯一（1:1）。
    2. 四个列表字段为 JSONB 数组；duration_estimate 为文本（如「约 12 分钟」）。
    3. content_raw 留存模型原始输出，便于排查。

实现逻辑：
    声明式映射；时间字段服务端默认 now()。
"""
from datetime import datetime

from sqlalchemy import BigInteger, String, Text, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class ChapterDramaScene(Base):
    """章节漫剧场景分析：导演 Agent 生成的四段式分镜素材。"""

    __tablename__ = "story_chapter_drama_scene"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    drama_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    novel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    scene_design: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    plot_arrangement: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    camera_movement: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    duration_estimate: Mapped[str | None] = mapped_column(String(255))
    content_raw: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
