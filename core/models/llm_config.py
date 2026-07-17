"""
大模型配置 ORM 模型（M9 大模型 API 管理）

整体思路：
    story_llm_config 表映射，承载多厂商模型配置（chat/embed/image 分型），是 M9 模型管理与
    M3 嵌入 / M7 对话 / M8 续写调用的配置来源；附带 story_llm_usage 只读映射供 M9 用量统计。

关键点：
    1. 表名严格为 story_llm_config / story_llm_usage，字段与 V1/V3 SQL 严格对齐。
    2. api_key 以 Fernet 密文存于 TEXT 字段；owner_id 可空表示全局配置（对全体用户可见只读）。
    3. llm_type 分 chat/embed/image；is_default 按「owner+类型」唯一；weight 越大降级优先级越高。

实现逻辑：
    声明式映射；JSONB 字段用 dict；时间字段服务端默认 now()；金额用 Numeric 精确存储。
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, String, Text, Integer, Boolean, DateTime, Numeric, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class LLMConfig(Base):
    """大模型配置：多厂商 chat/embed/image 模型的连接与降级参数。"""

    __tablename__ = "story_llm_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512))
    api_key: Mapped[str | None] = mapped_column(Text)  # Fernet 密文，切勿明文出参
    llm_type: Mapped[str] = mapped_column(String(16), default="chat", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    timeout: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMUsage(Base):
    """模型调用计费记录（M14 写入 / M9 用量统计只读）。"""

    __tablename__ = "story_llm_usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    config_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    model: Mapped[str | None] = mapped_column(String(128))
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
