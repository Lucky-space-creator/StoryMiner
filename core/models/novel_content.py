"""
小说内容相关 ORM 模型（M1）

整体思路：
    包含小说、章节、文档、解析任务、默认知识库五张表，均为 M1 小说解析范畴。

关键点：
    1. 表名严格为 story_novel / story_chapter / story_document / story_parse_task / story_knowledge_base。
    2. 所有写操作均带 owner_id 实现隔离；逻辑删除用 deleted_at。
    3. 文档/任务状态机字段对齐 API 契约（pending/parsing/.../done/failed）。

实现逻辑：
    声明式映射；JSONB 字段用 dict；时间字段用带时区 DateTime，服务端默认 now()。
"""
from datetime import datetime

from sqlalchemy import BigInteger, String, Text, Integer, Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Novel(Base):
    """小说：顶层隔离单元。"""

    __tablename__ = "story_novel"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str | None] = mapped_column(String(128))
    summary: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    cover: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="serial", nullable=False)
    tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(64))
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Chapter(Base):
    """章节：小说内容切分单元。"""

    __tablename__ = "story_chapter"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    volume: Mapped[str | None] = mapped_column(String(64))
    chapter_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeBase(Base):
    """知识库：每本小说可绑定一个或多个（M1 上传需绑定默认库「正文库」）。"""

    __tablename__ = "story_knowledge_base"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(16), default="private", nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Document(Base):
    """文档：上传原文，解析前 pending，解析后 done。"""

    __tablename__ = "story_document"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    novel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False)
    object_key: Mapped[str | None] = mapped_column(String(512))
    file_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ParseTask(Base):
    """解析异步任务：状态机 pending→parsing→splitting→done/failed。"""

    __tablename__ = "story_parse_task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    novel_id: Mapped[int | None] = mapped_column(BigInteger)
    doc_id: Mapped[int | None] = mapped_column(BigInteger)
    stage: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    celery_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
