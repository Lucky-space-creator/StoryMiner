"""
续写与概览 ORM 模型（M8 情节概览与续写）

整体思路：
    仅续写版本需要持久化（支撑 M8.6 多版本对比与 M8.7 入库采纳）；
    情节概览 / 时间线 / 角色弧线为实时 LLM 生成，不落库，保持最小存储。

关键点：
    1. 表名严格为 story_continue_write，带 owner_id 实现多用户隔离、deleted_at 逻辑删除。
    2. 记录续写时的上下文参数（基于章节、风格、长度、视角、用户提示），便于回溯与对比。
    3. content 为续写正文，word_count 冗余存储便于列表展示。

实现逻辑：
    声明式映射；时间字段带时区，服务端默认 now()。
"""
from datetime import datetime

from sqlalchemy import BigInteger, String, Text, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class ContinueWrite(Base):
    """续写版本：一次续写生成结果的快照（M8.6/M8.7）。"""

    __tablename__ = "story_continue_write"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    novel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chapter_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str | None] = mapped_column(String(255))
    style: Mapped[str] = mapped_column(String(16), default="original", nullable=False)
    length: Mapped[str] = mapped_column(String(16), default="mid", nullable=False)
    perspective: Mapped[str] = mapped_column(String(16), default="third", nullable=False)
    prompt: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
