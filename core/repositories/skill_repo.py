"""
Skill 数据访问（M10）

整体思路：
    封装 Skill 的读写与按挂载点查询启用项，可见性遵循「本人配置 + 全局内置」规则，
    内置项（builtin=True）仅可启停不可物理删除。

关键点：
    1. list_visible 返回本人或全局 Skill（供读取/挂载/调试）。
    2. list_enabled_by_mount 仅返回 enabled 且匹配挂载点的项，供流程注入附加指令。
    3. delete 拒绝删除内置项，避免破坏系统预置能力。

实现逻辑：
    基于 async session 的 select/delete；按 owner_id 与 mount_point 过滤。
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.skill import Skill


async def create(session: AsyncSession, skill: Skill) -> Skill:
    """写入 Skill（调用方提交）。"""
    session.add(skill)
    await session.flush()
    return skill


async def get_visible(session: AsyncSession, owner_id: int, skill_id: int) -> Skill | None:
    """读取可见 Skill：本人或全局。"""
    s = await session.get(Skill, skill_id)
    if s and (s.owner_id == owner_id or s.owner_id is None):
        return s
    return None


async def list_visible(session: AsyncSession, owner_id: int) -> list[Skill]:
    """Skill 列表：本人 + 全局，按 builtin/id 排序。"""
    stmt = select(Skill).where(
        (Skill.owner_id == owner_id) | (Skill.owner_id.is_(None))
    ).order_by(Skill.builtin.desc(), Skill.id.desc())
    return list((await session.execute(stmt)).scalars().all())


async def list_enabled_by_mount(session: AsyncSession, owner_id: int, mount_point: str) -> list[Skill]:
    """挂载点启用的 Skill（M10.4）：enabled 且挂载点匹配（含 global 全局挂载）。"""
    stmt = select(Skill).where(
        ((Skill.owner_id == owner_id) | (Skill.owner_id.is_(None))),
        Skill.enabled.is_(True),
        (Skill.mount_point == mount_point) | (Skill.mount_point == "global"),
    ).order_by(Skill.id.asc())
    return list((await session.execute(stmt)).scalars().all())


async def delete(session: AsyncSession, skill: Skill) -> None:
    """删除 Skill（内置项拒绝删除）。"""
    if skill.builtin:
        raise PermissionError("内置 Skill 不可删除，仅可启停")
    await session.delete(skill)
