"""
统一异步任务模型（所有向量/AI 耗时任务的进度聚合）

整体思路：
    把原先分散在三处的异步进度（解析入库、切割内存字典、图谱无记录）收敛到一张
    story_async_task 表，供仪表盘总览与前端全局轮询统一读取。

关键点：
    1. type 区分任务种类：parse(解析)/chunk(切割向量化)/graph(图谱抽取)/character(小传生成)。
    2. stage/progress/status 对齐统一状态机：pending→running→success/failed。
    3. 关联字段按需填写（novel_id/kb_id/doc_id/target_id），extra 存回放/重试所需上下文。
    4. 表名严格为 story_async_task，owner_id 实现数据隔离。
    5. V19：新增 estimated_duration_minutes / is_long_task / estimated_complete_at 字段，
       支持长耗时任务差异化展示与独立管理。

实现逻辑：
    声明式映射；JSONB 存 extra；时间字段带时区，服务端默认 now()。
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, String, Text, Integer, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class AsyncTask(Base):
    """统一异步任务：所有向量模型/AI 耗时任务共享的进度记录。"""

    __tablename__ = "story_async_task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    novel_id: Mapped[int | None] = mapped_column(BigInteger)
    kb_id: Mapped[int | None] = mapped_column(BigInteger)
    doc_id: Mapped[int | None] = mapped_column(BigInteger)
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    stage: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    # Token 消耗：后台任务边跑边回写，取消/失败时也能体现已消耗额度
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # V19：长耗时任务差异化 — 预估耗时分钟数、是否长任务（>10分钟）、预计完成时刻
    estimated_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_long_task: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    estimated_complete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
