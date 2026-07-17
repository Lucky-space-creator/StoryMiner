"""
提示词模板数据访问（M12）

整体思路：
    封装 PromptTemplate 读写与「设为默认」互斥，可见性遵循「本人模板」规则（仅本人可改）。

关键点：
    1. get_owned 仅返回本人模板（改删/默认/回滚前置校验）。
    2. clear_default 清空同「owner+type」旧默认，保证 is_default 唯一。
    3. list_templates 按 owner+type 过滤，version 倒序。

实现逻辑：
    基于 async session 的 select/update/delete。
"""
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.prompt_template import PromptTemplate


async def create(session: AsyncSession, tpl: PromptTemplate) -> PromptTemplate:
    """写入模板（调用方提交）。"""
    session.add(tpl)
    await session.flush()
    return tpl


async def get_owned(session: AsyncSession, owner_id: int, tpl_id: int) -> PromptTemplate | None:
    """读取本人模板（改删/默认/回滚前置校验）。"""
    tpl = await session.get(PromptTemplate, tpl_id)
    if tpl and tpl.owner_id == owner_id:
        return tpl
    return None


async def list_templates(session: AsyncSession, owner_id: int, ttype: str | None) -> list[PromptTemplate]:
    """模板列表：按 owner+type 过滤，version 倒序。"""
    stmt = select(PromptTemplate).where(PromptTemplate.owner_id == owner_id)
    if ttype:
        stmt = stmt.where(PromptTemplate.type == ttype)
    stmt = stmt.order_by(PromptTemplate.version.desc(), PromptTemplate.id.desc())
    return list((await session.execute(stmt)).scalars().all())


async def clear_default(session: AsyncSession, owner_id: int, ttype: str) -> None:
    """清空同「owner+type」旧默认，保证唯一。"""
    await session.execute(
        update(PromptTemplate).where(
            PromptTemplate.owner_id == owner_id,
            PromptTemplate.type == ttype,
            PromptTemplate.is_default.is_(True),
        ).values(is_default=False)
    )


async def delete_template(session: AsyncSession, tpl: PromptTemplate) -> None:
    """删除模板（物理删除）。"""
    await session.delete(tpl)
