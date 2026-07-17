"""
扩展功能 ORM 模型（M13 扩展功能）

整体思路：
    集中映射 M13 的六张扩展表：笔记(story_note)、标签(story_tag)、标签关联(story_tag_link)、
    收藏(story_favorite)、审计日志(story_audit_log)、阅读进度(story_reading_progress)；
    均以 owner_id 做数据隔离，供 extension_repo/service 复用。

关键点：
    1. 表名严格 story_xx，与 V6 迁移 SQL 对齐。
    2. owner_id 统一非空且加索引，保证按用户过滤高效。
    3. AuditLog.detail 用 JSONB 存操作快照；ReadingProgress 按 (owner_id, novel_id) 唯一。

实现逻辑：
    声明式映射；时间字段服务端默认 now()；可空字段用 Mapped[... | None]。
"""
from datetime import datetime

from sqlalchemy import BigInteger, String, Text, Integer, DateTime, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Note(Base):
    """笔记（M13.2）：可挂载到小说/章节/人物等目标。"""

    __tablename__ = "story_note"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Tag(Base):
    """标签（M13.4）：按用户维护，同名唯一。"""

    __tablename__ = "story_tag"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_story_tag_owner_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TagLink(Base):
    """标签关联（M13.4）：标签 ↔ 目标对象的多对多桥表。"""

    __tablename__ = "story_tag_link"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    tag_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Favorite(Base):
    """收藏（M13.6）：用户对任意目标的快捷收藏。"""

    __tablename__ = "story_favorite"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """审计日志（M13.7）：关键操作留痕，只增不改。"""

    __tablename__ = "story_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReadingProgress(Base):
    """阅读进度（M13.3）：按 (owner, novel) 记录最近阅读位置。"""

    __tablename__ = "story_reading_progress"
    __table_args__ = (UniqueConstraint("owner_id", "novel_id", name="uq_story_reading_owner_novel"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    novel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chapter_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
