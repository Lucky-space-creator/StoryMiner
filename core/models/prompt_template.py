"""
提示词模板 ORM 模型（M12 Prompt 编辑与管理）

整体思路：
    story_prompt_template 表映射，承载 Jinja2 模板（人设/抽取/续写/概览/自定义），
    是 M7 对话、M8 续写、M10 Skill 复用的提示词来源；版本历史存于 extra["history"] 以支持回滚。

关键点：
    1. 表名严格为 story_prompt_template，字段与 V2 SQL 严格对齐。
    2. type 取值 persona/extract/continue_write/summary/custom；is_default 按「owner+type」唯一。
    3. 版本历史写入 extra.history(JSONB)，避免新增版本表，满足 M12.6 回滚需求。

实现逻辑：
    声明式映射；extra 用 dict；时间字段服务端默认 now()。
"""
from datetime import datetime

from sqlalchemy import BigInteger, String, Text, Integer, Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class PromptTemplate(Base):
    """提示词模板：Jinja2 模板 + 版本管理，供 M7/M8/M10 复用。"""

    __tablename__ = "story_prompt_template"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # persona/extract/continue_write/summary/custom
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
