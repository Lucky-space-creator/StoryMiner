"""
提示词模板业务逻辑（M12）

整体思路：
    聚合模板 CRUD、版本管理、回滚、设为默认与渲染预览，统一返回契约友好的 dict。

关键点：
    1. 所有模板按 owner_id 隔离（仅本人可见可改）。
    2. 更新即 version+1，并将旧版本写入 extra.history 支持回滚（M12.6）。
    3. 渲染预览调用 prompt_render 引擎，返回渲染结果与变量/缺失清单。

实现逻辑：
    委托 prompt_repo 数据访问、prompt_render 渲染引擎；本层只做业务编排与字段映射。
"""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from schemas.prompt_template import (
    PromptTemplateCreate,
    PromptTemplateUpdate,
    PromptTemplateRender,
    PromptTemplateRollback,
)
from models.prompt_template import PromptTemplate
from repositories import prompt_repo
from services import prompt_render
from common.exceptions import BizError


def _iso(dt) -> str | None:
    """时间转 ISO 字符串。"""
    return dt.isoformat() if isinstance(dt, datetime) else None


def template_out(tpl: PromptTemplate) -> dict:
    """模板出参（暴露变量占位符）。"""
    return {
        "id": tpl.id, "owner_id": tpl.owner_id, "name": tpl.name,
        "description": tpl.description, "type": tpl.type, "content": tpl.content,
        "version": tpl.version, "is_default": tpl.is_default,
        "variables": prompt_render.extract_variables(tpl.content),
        "created_at": _iso(tpl.created_at), "updated_at": _iso(tpl.updated_at),
    }


async def create_template(session: AsyncSession, owner_id: int, data: PromptTemplateCreate) -> dict:
    """新建模板（M12.1/M12.2）：Jinja2 语法校验 + 初始版本历史。"""
    prompt_render.validate(data.content)
    history = [{"version": 1, "content": data.content, "description": data.description}]
    tpl = PromptTemplate(
        owner_id=owner_id, name=data.name, type=data.type, content=data.content,
        description=data.description, version=1, is_default=False,
        extra={"history": history},
    )
    await prompt_repo.create(session, tpl)
    await session.commit()
    await session.refresh(tpl)
    return template_out(tpl)


async def list_templates(session: AsyncSession, owner_id: int, ttype: str | None) -> list[dict]:
    """模板列表（M12.1，可按 type 过滤）。"""
    items = await prompt_repo.list_templates(session, owner_id, ttype)
    return [template_out(t) for t in items]


async def update_template(session: AsyncSession, owner_id: int, tpl_id: int, data: PromptTemplateUpdate) -> dict:
    """更新模板（M12.2/M12.6）：version+1，旧版本入历史。"""
    prompt_render.validate(data.content)
    tpl = await prompt_repo.get_owned(session, owner_id, tpl_id)
    if not tpl:
        raise BizError(404, "模板不存在")
    history = (tpl.extra or {}).get("history", [])
    history.append({"version": tpl.version, "content": tpl.content, "description": tpl.description})
    tpl.version += 1
    tpl.content = data.content
    if data.name is not None:
        tpl.name = data.name
    if data.description is not None:
        tpl.description = data.description
    tpl.extra = {**(tpl.extra or {}), "history": history}
    await session.commit()
    await session.refresh(tpl)
    return template_out(tpl)


async def delete_template(session: AsyncSession, owner_id: int, tpl_id: int) -> None:
    """删除模板（仅本人）。"""
    tpl = await prompt_repo.get_owned(session, owner_id, tpl_id)
    if not tpl:
        raise BizError(404, "模板不存在")
    await prompt_repo.delete_template(session, tpl)
    await session.commit()


async def set_default(session: AsyncSession, owner_id: int, tpl_id: int) -> dict:
    """设为某 type 默认模板（M12.7）：清旧默认后置位。"""
    tpl = await prompt_repo.get_owned(session, owner_id, tpl_id)
    if not tpl:
        raise BizError(404, "模板不存在")
    await prompt_repo.clear_default(session, owner_id, tpl.type)
    tpl.is_default = True
    await session.commit()
    await session.refresh(tpl)
    return template_out(tpl)


async def render_preview(session: AsyncSession, owner_id: int, tpl_id: int, data: PromptTemplateRender) -> dict:
    """调试预览（M12.5）：渲染完整 prompt + 变量/缺失清单。"""
    tpl = await prompt_repo.get_owned(session, owner_id, tpl_id)
    if not tpl:
        raise BizError(404, "模板不存在")
    variables = prompt_render.extract_variables(tpl.content)
    missing = [v for v in variables if v not in (data.context or {})]
    rendered = prompt_render.render(tpl.content, data.context or {})
    return {"rendered": rendered, "variables": variables, "missing": missing}


async def rollback(session: AsyncSession, owner_id: int, tpl_id: int, data: PromptTemplateRollback) -> dict:
    """回滚到指定版本（M12.6）：恢复历史内容并生成新版本，记录回滚动作。"""
    tpl = await prompt_repo.get_owned(session, owner_id, tpl_id)
    if not tpl:
        raise BizError(404, "模板不存在")
    history = (tpl.extra or {}).get("history", [])
    target = next((h for h in history if h.get("version") == data.version), None)
    if not target:
        raise BizError(404, f"未找到版本 {data.version}")
    history.append({
        "version": tpl.version, "content": tpl.content,
        "description": tpl.description, "rolled_back_from": data.version,
    })
    tpl.version += 1
    tpl.content = target["content"]
    tpl.extra = {**(tpl.extra or {}), "history": history}
    await session.commit()
    await session.refresh(tpl)
    return template_out(tpl)
