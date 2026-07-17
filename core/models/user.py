"""
用户 ORM 模型（story_user）

整体思路：
    映射 story_user 表，仅标识创建者，用于数据隔离，无角色权限。

关键点：
    1. 字段与 V1 SQL 严格对齐（表名 story_user）。
    2. settings/extra 为 JSONB，Python 侧用 dict。

实现逻辑：
    继承 Base，使用 Mapped/mapped_column 声明式映射。
"""
from datetime import datetime

from sqlalchemy import BigInteger, String, Text, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class User(Base):
    """系统用户（仅标识创建者 / 数据隔离，无角色权限分级）。"""

    __tablename__ = "story_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(64))
    avatar: Mapped[str | None] = mapped_column(String(512))
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
