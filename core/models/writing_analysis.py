"""
续写概览分析结果持久化模型（M8 缓存复用）

整体思路：
    概览 / 时间线 / 角色弧线三类分析结果按 (novel_id, owner_id, kind, detail) 各存一份最新，
    覆盖式更新（幂等），用于前端打开即读缓存、避免重复调用 LLM 浪费资源。

关键点：
    1. 表名严格为 story_writing_analysis；带 owner_id 隔离。
    2. kind ∈ {summary, timeline, character_arc}；detail ∈ {brief, detail} 控制详略。
    3. 唯一约束 (novel_id, owner_id, kind, detail) → upsert 同键覆盖，保留最新一份。

实现逻辑：
    声明式映射；时间字段服务端默认 now()。
"""
from datetime import datetime

from sqlalchemy import BigInteger, String, Text, Integer, DateTime, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class WritingAnalysis(Base):
    """概览类分析结果缓存（按小说 + 类型 + 详略各存一份最新）。"""

    __tablename__ = "story_writing_analysis"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    novel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # summary / timeline / character_arc
    detail: Mapped[str] = mapped_column(String(10), nullable=False, default="brief")  # brief / detail
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("owner_id", "novel_id", "kind", "detail", name="uq_writing_analysis_key"),
    )
