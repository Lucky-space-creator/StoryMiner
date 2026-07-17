"""
知识库 请求与响应模型（M2）

整体思路：
    定义知识库建库/更新/出参与统计出参，字段与 story_knowledge_base 表及 API 列表对齐。

关键点：
    1. KBCreate 需绑定 novel_id；scope 取 private/team/public。
    2. config 存切片策略等扩展配置（M3 复用）。
    3. 出参时间统一为 ISO 字符串。

实现逻辑：
    继承 pydantic BaseModel。
"""
from pydantic import BaseModel, Field


class KBCreate(BaseModel):
    """建库入参（M2.1）。"""
    novel_id: int
    name: str = Field(..., min_length=1, max_length=255)
    scope: str = "private"
    description: str | None = None
    config: dict = {}


class KBUpdate(BaseModel):
    """更新知识库入参（M2.4）。"""
    name: str | None = None
    scope: str | None = None
    description: str | None = None
    config: dict | None = None


class KBOut(BaseModel):
    """知识库出参。"""
    id: int
    novel_id: int
    owner_id: int
    name: str
    description: str | None = None
    scope: str
    config: dict = {}
    created_at: str | None = None
    updated_at: str | None = None


class KBStatsOut(BaseModel):
    """知识库统计出参（M2.9）。"""
    kb_id: int
    document_count: int
    chunk_count: int
    word_count: int
    updated_at: str | None = None
