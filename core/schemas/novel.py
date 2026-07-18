"""
小说/章节/文档/解析任务 请求与响应模型（M1）

整体思路：
    定义 M1 各资源的入参与出参，字段与 API 列表及数据库表对齐。

关键点：
    1. NovelCreate/NovelUpdate 覆盖元信息维护（M1.6）。
    2. ChapterSplit.offset 为字符偏移（从 1 开始）；ChapterMerge 传有序 id 列表。
    3. 出参时间统一为 ISO 字符串。

实现逻辑：
    继承 pydantic BaseModel。
"""
from pydantic import BaseModel, Field


class NovelCreate(BaseModel):
    """创建小说入参（M1.1）。"""
    name: str = Field(..., min_length=1, max_length=255)
    author: str | None = None
    summary: str | None = None
    tags: list[str] = []


class NovelUpdate(BaseModel):
    """更新小说元信息入参（M1.6）。"""
    name: str | None = None
    author: str | None = None
    summary: str | None = None
    description: str | None = None
    cover: str | None = None
    status: str | None = None
    tags: list[str] | None = None


class NovelOut(BaseModel):
    """小说出参。"""
    id: int
    owner_id: int
    name: str
    author: str | None = None
    summary: str | None = None
    ai_summary: str | None = None
    description: str | None = None
    cover: str | None = None
    status: str
    tags: list
    chapter_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class ChapterOut(BaseModel):
    """章节出参。"""
    id: int
    novel_id: int
    title: str | None = None
    volume: str | None = None
    chapter_no: int
    word_count: int
    char_start: int | None = None
    char_end: int | None = None
    created_at: str | None = None


class ChapterCorrect(BaseModel):
    """章节校正入参（M1.4）。"""
    title: str | None = None
    content: str | None = None


class ChapterSplit(BaseModel):
    """章节拆分入参：offset 为字符偏移（从 1 开始）。"""
    offset: int = Field(..., gt=0, description="字符偏移，从 1 开始")
    title: str | None = None


class ChapterMerge(BaseModel):
    """章节合并入参：有序章节 id 列表。"""
    chapter_ids: list[int]


class DocumentOut(BaseModel):
    """文档出参。"""
    id: int
    name: str
    doc_type: str
    status: str
    word_count: int
    created_at: str | None = None


class ParseTaskOut(BaseModel):
    """解析任务出参。"""
    id: int
    novel_id: int | None = None
    doc_id: int | None = None
    stage: str
    progress: int
    status: str
    error: str | None = None
    created_at: str | None = None
