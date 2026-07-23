"""极速模式分析摘要模型（M-极速）。

整体思路：
    极速模式（turbo）以 1~N 次大上下文 LLM 调用产出「人物画像 / 情节概览 / 关系概览」
    的散文/Markdown 摘要，存于本表，与深度模式（逐实体落结构化库）并存、互不影响。

关键点：
    1. 按 (novel_id, analysis_type) 唯一存一份最新摘要，重复触发即覆盖（幂等）。
    2. analysis_type ∈ {character, chapter, graph}，对应三种分析入口。
    3. 时间字段由应用层维护（created_at/updated_at），不依赖数据库触发器。

实现逻辑：
    继承 Base；novel_id 仅作索引不建外键，降低与 novel_content 表耦合。
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, Index
from sqlalchemy.sql import func
from datetime import datetime, timezone

from .base import Base


class StoryAnalysisSummary(Base):
    """极速模式分析摘要（按小说 + 类型存一份最新 Markdown 摘要）。"""

    __tablename__ = "story_analysis_summary"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, nullable=False, index=True)
    owner_id = Column(Integer, nullable=False, index=True)
    analysis_type = Column(String(20), nullable=False)  # character / chapter / graph
    mode = Column(String(20), nullable=False, default="turbo")
    content = Column(Text, nullable=False, default="")
    fmt = Column(String(10), nullable=False, default="markdown")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_summary_novel_type", "novel_id", "analysis_type"),
    )
