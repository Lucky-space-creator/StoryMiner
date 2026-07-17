"""
Skill 路由（/api/v1/skills）

整体思路：
    暴露 M10 全部接口：列表/新增/更新/启停/删除、调试、导入导出、内置库 seed。

关键点：
    1. 所有接口依赖 get_current_user 取得 owner_id，实现数据隔离。
    2. 调试 /debug 的 run 参数控制是否实际调 LLM。
    3. 内置库 /seed-builtin 供首次启动或手动补种。

实现逻辑：
    委托 skill_service 完成业务编排。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success
from services import skill_service

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillCreate(BaseModel):
    name: str
    description: str | None = None
    prompt_template: str
    trigger: str | None = None
    mount_point: str = "global"
    enabled: bool = True


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    prompt_template: str | None = None
    trigger: str | None = None
    mount_point: str | None = None
    enabled: bool | None = None


class DebugIn(BaseModel):
    context: dict = {}
    run: bool = False


class ImportIn(BaseModel):
    skills: list[dict]


@router.get("")
async def list_skills(user: User = Depends(get_current_user), session=Depends(get_session)):
    """Skill 列表（M10.1）。"""
    return success(await skill_service.list_skills(session, user.id))


@router.post("")
async def create(data: SkillCreate, user: User = Depends(get_current_user), session=Depends(get_session)):
    """新建 Skill（M10.1）。"""
    return success(await skill_service.create_skill(session, user.id, data.model_dump()), "创建成功")


@router.put("/{skill_id}")
async def update(skill_id: int, data: SkillUpdate, user: User = Depends(get_current_user), session=Depends(get_session)):
    """更新 Skill（M10.1）。"""
    return success(await skill_service.update_skill(session, user.id, skill_id, data.model_dump(exclude_unset=True)))


@router.post("/{skill_id}/toggle")
async def toggle(skill_id: int, enabled: bool, user: User = Depends(get_current_user), session=Depends(get_session)):
    """启停 Skill（M10.2/M10.4）。"""
    return success(await skill_service.set_enabled(session, user.id, skill_id, enabled))


@router.delete("/{skill_id}")
async def delete(skill_id: int, user: User = Depends(get_current_user), session=Depends(get_session)):
    """删除 Skill（M10.1，内置项拒绝）。"""
    await skill_service.delete_skill(session, user.id, skill_id)
    return success(msg="已删除")


@router.post("/{skill_id}/debug")
async def debug(skill_id: int, data: DebugIn, user: User = Depends(get_current_user), session=Depends(get_session)):
    """调试 Skill（M10.5）：渲染预览 + 可选实际调用。"""
    return success(await skill_service.debug_skill(session, user.id, skill_id, data.context, data.run))


@router.post("/seed-builtin")
async def seed_builtin(user: User = Depends(get_current_user), session=Depends(get_session)):
    """补种内置 Skill 库（M10.2）。"""
    n = await skill_service.seed_builtin(session)
    return success({"count": n}, f"已补种 {n} 个内置 Skill")


@router.get("/export")
async def export_skills(user: User = Depends(get_current_user), session=Depends(get_session)):
    """导出 Skill 为 JSON（M10.6）。"""
    return success(await skill_service.export_skills(session, user.id))


@router.post("/import")
async def import_skills(data: ImportIn, user: User = Depends(get_current_user), session=Depends(get_session)):
    """导入 Skill（M10.6）。"""
    n = await skill_service.import_skills(session, user.id, data.skills)
    return success({"count": n}, f"已导入 {n} 个 Skill")
