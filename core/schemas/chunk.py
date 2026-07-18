"""
切片 请求模型（M3）

整体思路：
    定义切割触发与策略更新的入参，字段对齐 M3 接口契约。

关键点：
    1. strategy 限定 chapter/length；size/overlap 为可选切片参数。

实现逻辑：
    继承 pydantic BaseModel。
"""
from pydantic import BaseModel, Field


class ChunkRequest(BaseModel):
    """切割/重建/增量索引入参（M3.1/M3.3/M3.4）。"""
    strategy: str = Field("length", description="chapter/length")
    size: int = Field(800, gt=0, description="切片长度(字符)")
    overlap: int = Field(0, ge=0, description="重叠字符数")
    embed_config_id: int | None = Field(None, description="指定嵌入模型配置 id（llm_type=embed）；不传则用默认分发链")


class ChunkStrategyUpdate(BaseModel):
    """切片策略更新入参（M3.2）。"""
    strategy: str = Field("length", description="chapter/length")
    size: int = Field(800, gt=0)
    overlap: int = Field(0, ge=0)
