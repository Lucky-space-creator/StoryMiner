"""
切片 ORM 模型（M3 切割向量化 / M4 切片展示）

整体思路：
    story_chunk 表映射，承载文档切割后的文本片段与向量引用，是 M3 向量化与 M4 展示的核心载体。

关键点：
    1. 表名严格为 story_chunk；关联 doc/novel/kb/chapter，全部带 owner_id 隔离。
    2. vector_id 指向 pgvector 记录（M3 写入），meta 存来源定位（章节/字符区间）。
    3. disabled 支持屏蔽切片（M4.7）；story_chunk 无 deleted_at，删除为物理删除。

实现逻辑：
    声明式映射；JSONB 字段用 dict/默认工厂；时间字段服务端默认 now()。
"""
from datetime import datetime

from sqlalchemy import BigInteger, String, Text, Integer, Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from models.base import Base


class PgVectorStr(TypeDecorator):
    """pgvector 向量列：以字符串形式存取，规避 asyncpg 自定义类型注册。

    关键点：
        1. impl=Text，ORM 侧按字符串读写；实际库列为 vector（见 V4 SQL）。
        2. 写入将 list[float] 序列化为 '[x,y,z]'；读取反序列化为 list[float]。
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """入参 list[float] → pgvector 字符串字面量。"""
        if value is None:
            return None
        return "[" + ",".join(str(float(x)) for x in value) + "]"

    def process_result_value(self, value, dialect):
        """出参 pgvector 字符串 → list[float]。"""
        if value is None:
            return None
        return [float(x) for x in value.strip("[]").split(",") if x.strip() != ""]


class Chunk(Base):
    """切片：文档切割后的最小检索单元。"""

    __tablename__ = "story_chunk"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    novel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kb_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chapter_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    vector_id: Mapped[str | None] = mapped_column(String(128))
    # 向量列：Chroma 模式下不写入（向量存于向量库），保留以便后续切回 pgvector 零改表。
    embedding: Mapped[list | None] = mapped_column(PgVectorStr, nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
