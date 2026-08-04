"""
大模型配置路由（/api/v1/llm-configs）

整体思路：
    暴露 M9 全部接口：配置列表/新增/更新/删除、健康检查、设为默认、用量统计。

关键点：
    1. 所有接口依赖 get_current_user 取得 owner_id，实现数据隔离。
    2. /usage 静态路径需先于 /{id} 声明，避免被当作 id 解析。
    3. 响应统一 {code,msg,data}；密钥出参隐藏明文。

实现逻辑：
    委托 llm_service 完成业务编排。
"""
from fastapi import APIRouter, Depends, Query

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success
from schemas.llm_config import LLMConfigCreate, LLMConfigUpdate
from services import llm_service

router = APIRouter(prefix="/llm-configs", tags=["llm-configs"])


@router.get("")
async def list_configs(
    llm_type: str | None = Query(None, description="chat/embed/image"),
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """配置列表（M9.1，可按 llm_type 过滤）。"""
    return success(await llm_service.list_configs(session, user.id, llm_type))


@router.post("")
async def create_config(
    data: LLMConfigCreate,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """新增配置（M9.1/M9.2，api_key 加密存储）。"""
    return success(await llm_service.create_config(session, user.id, data), "创建成功")


@router.get("/usage")
async def usage(user: User = Depends(get_current_user), session=Depends(get_session)):
    """配额与成本统计（M9.5，对接 M14）。"""
    return success(await llm_service.usage(session, user.id))


@router.put("/{cfg_id}")
async def update_config(
    cfg_id: int, data: LLMConfigUpdate,
    user: User = Depends(get_current_user), session=Depends(get_session),
):
    """更新配置。"""
    return success(await llm_service.update_config(session, user.id, cfg_id, data))


@router.delete("/{cfg_id}")
async def delete_config(cfg_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """删除配置。"""
    await llm_service.delete_config(session, user.id, cfg_id)
    return success(msg="已删除")


@router.post("/{cfg_id}/health")
async def health(cfg_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """健康检查（M9.6，连通+延迟）。"""
    return success(await llm_service.health(session, user.id, cfg_id))


@router.post("/{cfg_id}/set-default")
async def set_default(cfg_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """设为某类型默认模型（M9.3）。"""
    return success(await llm_service.set_default(session, user.id, cfg_id), "已设为默认")


@router.post("/{cfg_id}/cancel-default")
async def cancel_default(cfg_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """取消某类型默认模型（M9.3）：该类型将无默认。"""
    return success(await llm_service.cancel_default(session, user.id, cfg_id), "已取消默认")
