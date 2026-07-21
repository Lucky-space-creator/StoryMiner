"""
知识图谱数据访问（M5）

整体思路：
    封装 Entity / Relation / RelationType / EntityType 的读写，所有查询强制 owner 隔离（关系类型含系统内置），
    提供「存在则取、不存在则建」的 upsert 语义，支撑 LLM 抽取的幂等落库。

关键点：
    1. entity 按 (novel_id, name, type) 去重；relation 按 (source_id, target_id, type) 去重。
    2. relation_type 取「系统内置(owner_id IS NULL) + 本人」，并支持按 code 取/建。
    3. entity_type 取所有内置类型，支持按 code 查询。
    4. 组装图谱时按 novel 批量取出实体与关系，避免 N+1。
    5. 提供「按小说批量删除实体/关系」方法，支持 V13 先删后建策略。

实现逻辑：
    基于 async session 的 select/delete；upsert 先查后建，调用方统一提交。
"""
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from models.graph import Entity, Relation, RelationType, EntityType


async def list_relation_types(session: AsyncSession, owner_id: int) -> list[RelationType]:
    """关系类型字典：系统内置 + 本人自定义。"""
    stmt = select(RelationType).where(
        (RelationType.owner_id == owner_id) | (RelationType.owner_id.is_(None))
    ).order_by(RelationType.builtin.desc(), RelationType.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_relation_type_by_code(session: AsyncSession, code: str) -> RelationType | None:
    """按 code 取关系类型（含内置）。"""
    stmt = select(RelationType).where(RelationType.code == code)
    return (await session.execute(stmt)).scalars().first()


async def create_relation_type(session: AsyncSession, rt: RelationType) -> RelationType:
    """写入关系类型（调用方提交）。"""
    session.add(rt)
    await session.flush()
    return rt


# ───────────────────────────── 实体类型字典（V13 新增） ─────────────────────────────

async def list_entity_types(session: AsyncSession) -> list[EntityType]:
    """实体类型字典：所有内置类型，按 ID 排序。"""
    stmt = select(EntityType).order_by(EntityType.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_entity_type_by_code(session: AsyncSession, code: str) -> EntityType | None:
    """按 code 取实体类型。"""
    stmt = select(EntityType).where(EntityType.code == code)
    return (await session.execute(stmt)).scalars().first()


# ───────────────────────────── 实体 CRUD ─────────────────────────────

async def list_entities(session: AsyncSession, novel_id: int) -> list[Entity]:
    """查询小说实体（过滤逻辑删除），按 ID 排序。"""
    stmt = select(Entity).where(
        Entity.novel_id == novel_id, Entity.deleted_at.is_(None)
    ).order_by(Entity.id)
    return list((await session.execute(stmt)).scalars().all())


async def count_entities_by_novel(session: AsyncSession, novel_id: int) -> tuple[int, int]:
    """统计小说实体数与关系数，返回 (entity_count, relation_count)。
    供前端检测图谱是否已存在数据，用于抽取前的确认提示。
    """
    from sqlalchemy import func
    ent_count = (await session.execute(
        select(func.count()).select_from(Entity).where(
            Entity.novel_id == novel_id, Entity.deleted_at.is_(None)
        )
    )).scalar() or 0
    rel_count = (await session.execute(
        select(func.count()).select_from(Relation).where(Relation.novel_id == novel_id)
    )).scalar() or 0
    return ent_count, rel_count


async def get_entity(session: AsyncSession, novel_id: int, name: str, etype: str) -> Entity | None:
    """按 (novel_id, name, type) 查实体。"""
    stmt = select(Entity).where(
        Entity.novel_id == novel_id,
        Entity.name == name,
        Entity.type == etype,
        Entity.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalars().first()


async def create_entity(session: AsyncSession, e: Entity) -> Entity:
    """写入实体（调用方提交）。"""
    session.add(e)
    await session.flush()
    return e


async def delete_entities_by_novel(session: AsyncSession, novel_id: int) -> int:
    """物理删除小说下所有实体（V13 先删后建策略）。
    注意：必须先删关系再删实体，否则外键约束会阻止删除。
    """
    # 先删关系
    rel_result = await session.execute(
        delete(Relation).where(Relation.novel_id == novel_id)
    )
    rel_count = rel_result.rowcount
    # 再删实体
    ent_result = await session.execute(
        delete(Entity).where(Entity.novel_id == novel_id)
    )
    ent_count = ent_result.rowcount
    await session.flush()
    return ent_count


async def delete_relations_by_novel(session: AsyncSession, novel_id: int) -> int:
    """物理删除小说下所有关系。"""
    result = await session.execute(
        delete(Relation).where(Relation.novel_id == novel_id)
    )
    await session.flush()
    return result.rowcount


async def get_entity_by_id(session: AsyncSession, entity_id: int) -> Entity | None:
    """按 ID 查实体（含逻辑删除过滤）。"""
    stmt = select(Entity).where(Entity.id == entity_id, Entity.deleted_at.is_(None))
    return (await session.execute(stmt)).scalars().first()


# ───────────────────────────── 关系 CRUD ─────────────────────────────

async def list_relations(session: AsyncSession, novel_id: int) -> list[Relation]:
    """查询小说关系，按 ID 排序。"""
    stmt = select(Relation).where(Relation.novel_id == novel_id).order_by(Relation.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_relation(session: AsyncSession, source_id: int, target_id: int, rtype: str) -> Relation | None:
    """按 (source_id, target_id, type) 查关系。"""
    stmt = select(Relation).where(
        Relation.source_id == source_id,
        Relation.target_id == target_id,
        Relation.type == rtype,
    )
    return (await session.execute(stmt)).scalars().first()


async def create_relation(session: AsyncSession, r: Relation) -> Relation:
    """写入关系（调用方提交）。"""
    session.add(r)
    await session.flush()
    return r


async def get_relation_by_id(session: AsyncSession, relation_id: int) -> Relation | None:
    """按 ID 查关系。"""
    stmt = select(Relation).where(Relation.id == relation_id)
    return (await session.execute(stmt)).scalars().first()


async def delete_entity(session: AsyncSession, e: Entity) -> None:
    """物理删除实体（调用方提交）。"""
    await session.delete(e)
    await session.flush()


async def delete_relation(session: AsyncSession, r: Relation) -> None:
    """物理删除关系（调用方提交）。"""
    await session.delete(r)
    await session.flush()
