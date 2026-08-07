"""
知识图谱 ORM 模型（M5 实体关系抽取与图谱展示）

整体思路：
    映射 story_entity / story_relation / story_relation_type / story_entity_type 四张表，
    承载小说知识图谱的实体、关系、关系类型字典与实体类型字典，是 M5 抽取与展示的数据载体。

关键点：
    1. story_entity.type 已扩展为 VARCHAR(32)，支持7种实体类型（V13迁移）。
    2. 实体按 (novel_id, name, type) 唯一；关系按 (source_id, target_id, type) 唯一，避免重复抽取翻倍。
    3. 关系类型字典支持系统内置（owner_id IS NULL）与用户自定义，供前端关系着色与图例。
    4. 实体类型字典（story_entity_type）定义7种内置类型及其颜色/符号。

实现逻辑：
    声明式映射；JSONB 用 dict；时间字段服务端默认 now()；逻辑删除用 deleted_at。
"""
from datetime import datetime

from sqlalchemy import BigInteger, String, Text, Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class RelationType(Base):
    """关系类型字典：系统内置或用户自定义的关系类别。"""

    __tablename__ = "story_relation_type"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger)  # NULL=系统内置
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str] = mapped_column(String(16), default="#888888")
    description: Mapped[str | None] = mapped_column(Text)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EntityType(Base):
    """实体类型字典：系统内置7种实体类型（人物/地点/组织/时间/事件/物品/概念）。"""

    __tablename__ = "story_entity_type"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str] = mapped_column(String(16), default="#888888", nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), default="circle", nullable=False)
    builtin: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Entity(Base):
    """实体：小说中的图谱节点，支持7种类型（人物/地点/组织/时间/事件/物品/概念）。"""

    __tablename__ = "story_entity"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), default="character", nullable=False)
    profile: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    avatar: Mapped[str | None] = mapped_column(String(512))
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Relation(Base):
    """关系：实体之间的有向边（source -> target）。"""

    __tablename__ = "story_relation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
