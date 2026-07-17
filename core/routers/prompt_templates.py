"""
提示词模板路由（/api/v1/prompt-templates）

整体思路：
    暴露 M12 全部接口：模板列表/新建/更新/删除、渲染预览、版本回滚、设为默认。

关键点：
    1. 依赖 get_current_user 取得 owner_id，实现数据隔离。
    2. 静态子路径 /{id}/render、/{id}/rollback、/{id}/set-default 在 /{id} 之前声明，避免歧义。
    3. 响应统一 {code,msg,data}。

实现逻辑：
    委托 prompt_service 完成业务编排。
"""
from fastapi import APIRouter, Depends, Query

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success
from schemas.prompt_template import (
    PromptTemplateCreate,
    PromptTemplateUpdate,
    PromptTemplateRender,
    PromptTemplateRollback,
)
from services import prompt_service

router = APIRouter(prefix="/prompt-templates", tags=["prompt-templates"])


@router.get("")
async def list_templates(
    type: str | None = Query(None, description="persona/extract/continue_write/summary/custom"),
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """模板列表（M12.1，可按 type 过滤）。"""
    return success(await prompt_service.list_templates(session, user.id, type))


@router.post("")
async def create_template(
    data: PromptTemplateCreate,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """新建模板（M12.1/M12.2，Jinja2 校验）。"""
    return success(await prompt_service.create_template(session, user.id, data), "创建成功")


@router.post("/{tpl_id}/render")
async def render_preview(
    tpl_id: int, data: PromptTemplateRender,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """调试预览：填上下文→渲染完整 prompt（M12.5）。"""
    return success(await prompt_service.render_preview(session, user.id, tpl_id, data))


@router.post("/{tpl_id}/rollback")
async def rollback(
    tpl_id: int, data: PromptTemplateRollback,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """回滚到指定 version（M12.6）。"""
    return success(await prompt_service.rollback(session, user.id, tpl_id, data), "已回滚")


@router.post("/{tpl_id}/set-default")
async def set_default(
    tpl_id: int,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """设为该 type 默认模板（M12.7）。"""
    return success(await prompt_service.set_default(session, user.id, tpl_id), "已设为默认")


@router.put("/{tpl_id}")
async def update_template(
    tpl_id: int, data: PromptTemplateUpdate,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """更新模板（version+1，M12.2/M12.6）。"""
    return success(await prompt_service.update_template(session, user.id, tpl_id, data))


@router.delete("/{tpl_id}")
async def delete_template(tpl_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """删除模板。"""
    await prompt_service.delete_template(session, user.id, tpl_id)
    return success(msg="已删除")
