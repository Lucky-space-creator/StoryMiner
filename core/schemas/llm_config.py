"""
大模型配置 请求与响应模型（M9）

整体思路：
    定义模型配置的建/改/出参与健康检查、用量统计出参，字段与 story_llm_config 表及 API 列表对齐。

关键点：
    1. Create 需 provider/model/llm_type；本地 Ollama 可不传 api_key。
    2. 出参隐藏明文 api_key，仅返回 has_key 标记，杜绝密钥泄漏。
    3. 时间统一 ISO 字符串。

实现逻辑：
    继承 pydantic BaseModel。
"""
from pydantic import BaseModel, Field


class LLMConfigCreate(BaseModel):
    """新增模型配置入参（M9.1）。"""
    name: str = Field(..., min_length=1, max_length=128)
    provider: str = Field(..., min_length=1, max_length=32)   # OpenAI/Claude/Ollama/智谱/通义
    model: str = Field(..., min_length=1, max_length=128)
    base_url: str | None = None
    api_key: str | None = None
    llm_type: str = "chat"                       # chat/embed/image
    weight: int = 0
    timeout: int = 60
    extra: dict = {}
    temperature: float = 0.7
    max_tokens: int | None = None


class LLMConfigUpdate(BaseModel):
    """更新模型配置入参（api_key 传入则覆盖，留空不动）。"""
    name: str | None = None
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    llm_type: str | None = None
    weight: int | None = None
    timeout: int | None = None
    status: str | None = None
    extra: dict | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class LLMConfigOut(BaseModel):
    """模型配置出参（隐藏密钥明文，仅暴露 has_key）。"""
    id: int
    owner_id: int | None = None
    name: str
    provider: str
    model: str
    base_url: str | None = None
    has_key: bool = False
    llm_type: str
    is_default: bool
    weight: int
    timeout: int
    status: str
    extra: dict = {}
    temperature: float = 0.7
    max_tokens: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class HealthOut(BaseModel):
    """健康检查出参（M9.6）：连通性 + 延迟。"""
    id: int
    ok: bool
    latency_ms: int | None = None
    detail: str | None = None
