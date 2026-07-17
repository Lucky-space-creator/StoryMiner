"""
大模型 Skill 配置 ORM 模型（M10 大模型 Skill 管理）

整体思路：
    story_skill 表映射可复用提示词任务包，是 M10 的核心实体；通过 mount_point 决定挂载点，
    供 M7 对话 / M8 续写 / 抽取流程加载为附加指令；builtin 标记内置库（可启停不可删）。

关键点：
    1. 表名严格为 story_skill，字段与 V3 SQL 对齐。
    2. mount_point ∈ {dialogue, continue_write, extract, global}；enabled 控制是否生效。
    3. owner_id 可空表示全局内置 Skill；用户自建必带 owner_id 隔离。
    4. prompt_template 为 Jinja2 模板，变量由 mount_point 约定（见 skill_service）。

实现逻辑：
    声明式映射；时间字段服务端默认 now()；builtin 默认 False（用户自建）。
"""
from datetime import datetime

from sqlalchemy import BigInteger, String, Text, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Skill(Base):
    """可复用提示词任务包（M10）。"""

    __tablename__ = "story_skill"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str | None] = mapped_column(String(128))  # 触发条件描述
    mount_point: Mapped[str] = mapped_column(String(32), default="global", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
