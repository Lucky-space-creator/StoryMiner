"""
人物档案 ORM 模型（M6 小说人物信息简介）

整体思路：
    映射 V5 新建的 story_character 表，承载小说人物的详情/小传/画像，与 M5 的
    story_entity（图谱节点）解耦；本表面向「人物卡」展示与维护，支持手动编辑与 LLM 生成。

关键点：
    1. 表名严格为 story_character（V5 新建，本文件仅补 ORM 映射）。
    2. 同小说内人物名唯一（UNIQUE(novel_id, name)），避免重复建档。
    3. 字段覆盖 M6.1 档案要素（性别/身份/性格/外貌/口头禅）+ 小传(description) + 出场次数。

实现逻辑：
    声明式映射；JSONB 用 dict；时间字段服务端默认 now()；逻辑删除用 deleted_at。
"""
from datetime import datetime

from sqlalchemy import BigInteger, String, Text, Integer, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Character(Base):
    """人物档案：小说中可维护详情的「人物卡」。"""

    __tablename__ = "story_character"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="配角", nullable=False)
    gender: Mapped[str | None] = mapped_column(String(16))
    identity: Mapped[str | None] = mapped_column(String(255))
    personality: Mapped[str | None] = mapped_column(String(512))
    appearance: Mapped[str | None] = mapped_column(String(512))
    catchphrase: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    avatar: Mapped[str | None] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    appearances: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
