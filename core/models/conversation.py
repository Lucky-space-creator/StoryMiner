"""
对话会话 ORM 模型（M7 用户 × 小说人物对话）

整体思路：
    映射 V1 已建的 story_conversation 与 story_conversation_message 两表，承载会话元信息
    与多轮消息；会话记录自选人物(character_ids JSONB)与 system_prompt 快照，消息按 conv 归属。

关键点：
    1. 表名严格为 story_conversation / story_conversation_message（V1 已建，本文件仅补 ORM 映射）。
    2. character_ids 存人物主键数组，群聊即长度>1；message.character_id 标记群聊发言者。
    3. 时间字段服务端默认 now()；消息无逻辑删除，会话删除级联清消息。

实现逻辑：
    声明式映射；JSONB 用 list/dict；会话与消息一对多，由 service 层按 owner 隔离组装。
"""
from datetime import datetime

from sqlalchemy import BigInteger, String, Text, Integer, Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Conversation(Base):
    """对话会话：用户与一个或多个小说人物的对话容器。"""

    __tablename__ = "story_conversation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    novel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    character_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    llm_config_id: Mapped[int | None] = mapped_column(BigInteger)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationMessage(Base):
    """对话消息：单轮 user/assistant 内容，按 conv 归属与创建时间排序。"""

    __tablename__ = "story_conversation_message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conv_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    character_id: Mapped[int | None] = mapped_column(BigInteger)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
