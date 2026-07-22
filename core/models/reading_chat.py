"""
阅读辅助对话 ORM 模型。

整体思路：直接复用项目既有的 story_conversation / story_conversation_message 两张表（已含
owner_id 用户隔离、novel_id 按小说隔离），仅映射 V16 迁移新增的扩展字段，不重建表。

关键点：
1. ReadingConversation 对应 story_conversation，承载每 (用户, 小说) 唯一会话。
2. ReadingChatMessage 对应 story_conversation_message，承载单条消息与附件。
3. context_window / keep_recent / compressed_summary 支撑上下文窗口限制与自动压缩。
4. attachments(jsonb) 支撑图片/文件上传。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class ReadingConversation(Base):
    """阅读辅助对话会话：每 (owner_id, novel_id) 唯一一条。"""

    __tablename__ = "story_conversation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    novel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    character_ids: Mapped[list] = mapped_column(JSONB, default=list)
    llm_config_id: Mapped[int | None] = mapped_column(BigInteger)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    # —— V16 扩展字段 ——
    context_window: Mapped[int] = mapped_column(Integer, default=4000)
    keep_recent: Mapped[int] = mapped_column(Integer, default=10)
    compressed_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReadingChatMessage(Base):
    """阅读辅助对话单条消息。"""

    __tablename__ = "story_conversation_message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conv_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("story_conversation.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str | None] = mapped_column(Text)
    character_id: Mapped[int | None] = mapped_column(BigInteger)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    # —— V16 扩展字段 ——
    attachments: Mapped[list] = mapped_column(JSONB, default=list)
    is_compressed: Mapped[bool] = mapped_column(Boolean, default=False)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
