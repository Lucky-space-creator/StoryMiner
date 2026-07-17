"""
提示词模板 请求与响应模型（M12）

整体思路：
    定义模板建/改/渲染/回滚入参与出参，字段与 story_prompt_template 表及 API 列表对齐。

关键点：
    1. Create 需 name/type/content；type 限定枚举语义（persona/extract/continue_write/summary/custom）。
    2. Render 入参为任意上下文 dict（Jinja2 变量填充）。
    3. 出参额外暴露 variables（从模板抽取出的占位符）便于前端提示。

实现逻辑：
    继承 pydantic BaseModel。
"""
from pydantic import BaseModel, Field
from typing import Any


class PromptTemplateCreate(BaseModel):
    """新建模板入参（M12.1/M12.2）。"""
    name: str = Field(..., min_length=1, max_length=128)
    type: str = Field(..., min_length=1, description="persona/extract/continue_write/summary/custom")
    content: str = Field(..., min_length=1, description="Jinja2 模板")
    description: str | None = None


class PromptTemplateUpdate(BaseModel):
    """更新模板入参（version+1）。content 必填以产生新版本。"""
    name: str | None = None
    description: str | None = None
    content: str = Field(..., description="Jinja2 模板，更新即生成新版本")


class PromptTemplateRender(BaseModel):
    """渲染预览入参（M12.5）：上下文变量 dict。"""
    context: dict[str, Any] = {}


class PromptTemplateRollback(BaseModel):
    """回滚入参（M12.6）：目标版本号。"""
    version: int = Field(..., gt=0, description="回滚到的历史版本号")


class PromptTemplateOut(BaseModel):
    """模板出参（暴露变量占位符）。"""
    id: int
    owner_id: int
    name: str
    description: str | None = None
    type: str
    content: str
    version: int
    is_default: bool
    variables: list[str] = []
    created_at: str | None = None
    updated_at: str | None = None


class RenderOut(BaseModel):
    """渲染结果出参（M12.5）。"""
    rendered: str
    variables: list[str] = []   # 模板声明的全部变量
    missing: list[str] = []     # 上下文缺失的变量
