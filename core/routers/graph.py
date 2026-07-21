"""
知识图谱路由（M5）

整体思路：
    暴露 M5 接口：图谱查询（按小说组装节点/边）、实体关系抽取（后台任务）、
    图谱存在检测（V13新增）、实体类型字典、关系类型字典。
    图谱与抽取挂在 /novels 前缀下（与小说资源同域），类型字典独立暴露。

关键点：
    1. 所有写接口依赖 get_current_user 取得 owner_id，实现数据隔离。
    2. 抽取为耗时操作，使用 BackgroundTasks 后台执行，接口立即返回已触发状态。
    3. V13 新增 /graph/exists 供前端检测已有数据，抽取前二次确认。
    4. 响应统一 {code,msg,data}。

实现逻辑：
    委托 graph_service；抽取后台任务自开 SessionLocal 落库，避免阻塞请求。
"""
from fastapi import APIRouter, BackgroundTasks, Depends, Body

from models.user import User
from db import get_session
from auth.jwt import get_current_user
from common.response import success
from common.exceptions import BizError
from common import task_queue
from services import graph_service, task_service
from repositories import novel_repo

# 图谱与抽取：与小说资源同域，挂在 /novels 下
router = APIRouter(prefix="/novels", tags=["graph"])

# 关系类型字典：独立端点（/api/v1/relation-types）
rt_router = APIRouter(tags=["relation-types"])

# 实体类型字典：独立端点（/api/v1/entity-types）
et_router = APIRouter(tags=["entity-types"])


@router.get("/{novel_id}/graph")
async def get_graph(
    novel_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """知识图谱查询（M5.1）：组装节点/关系/类别供前端 ECharts 渲染。"""
    graph = await graph_service.get_graph(session, novel_id, user.id)
    return success(graph)


@router.get("/{novel_id}/graph/exists")
async def check_graph_exists(
    novel_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """图谱存在检测（V13新增）：返回实体/关系数量，供前端抽取前确认。"""
    novel = await novel_repo.get_novel(session, user.id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")
    info = await graph_service.check_graph_exists(session, novel_id)
    return success(info)


@router.post("/{novel_id}/extract")
async def extract_graph(
    novel_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """实体关系抽取（M5.2 V13重设计）：先清空旧数据，再分块抽取7种实体类型。
    后台调用 LLM 抽取并落库，接口立即返回统一 task_id。
    """
    novel = await novel_repo.get_novel(session, user.id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")
    task = await task_service.create_task(
        session, user.id, "graph",
        f"小说{novel.name}-知识图谱抽取", novel_id=novel_id)
    task_queue.submit(graph_service.run_extract, novel_id, user.id, task.id)
    return success({"task_id": task.id}, "已启动实体关系抽取（将先清空旧数据后重新抽取）")


@rt_router.get("/relation-types")
async def list_relation_types(
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """关系类型字典（M5.3）：系统内置 + 本人自定义。"""
    types = await graph_service.list_relation_types(session, user.id)
    return success([
        {"id": t.id, "code": t.code, "label": t.label, "color": t.color, "builtin": t.builtin}
        for t in types
    ])


@et_router.get("/entity-types")
async def list_entity_types(
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """实体类型字典（V13新增）：7种内置类型及其颜色/符号。"""
    types = await graph_service.list_entity_types(session)
    return success([
        {"id": t.id, "code": t.code, "label": t.label, "color": t.color, "symbol": t.symbol}
        for t in types
    ])


@router.post("/{novel_id}/entities")
async def create_entity(
    novel_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """新增实体（M5 增强）。"""
    data = await graph_service.create_entity(session, novel_id, user.id, payload)
    return success(data, "实体已创建")


@router.put("/{novel_id}/entities/{entity_id}")
async def update_entity(
    novel_id: int,
    entity_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """修改实体（M5 增强）。"""
    data = await graph_service.update_entity(session, user.id, entity_id, payload)
    return success(data, "实体已更新")


@router.delete("/{novel_id}/entities/{entity_id}")
async def delete_entity(
    novel_id: int,
    entity_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """删除实体（物理删除，级联删关系）。"""
    data = await graph_service.delete_entity(session, user.id, entity_id)
    return success(data, "实体已删除")


@router.post("/{novel_id}/relations")
async def create_relation(
    novel_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """新增关系（M5 增强）。"""
    data = await graph_service.create_relation(session, novel_id, user.id, payload)
    return success(data, "关系已创建")


@router.put("/{novel_id}/relations/{relation_id}")
async def update_relation(
    novel_id: int,
    relation_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """修改关系（M5 增强）。"""
    data = await graph_service.update_relation(session, user.id, relation_id, payload)
    return success(data, "关系已更新")


@router.delete("/{novel_id}/relations/{relation_id}")
async def delete_relation(
    novel_id: int,
    relation_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """删除关系（物理删除）。"""
    data = await graph_service.delete_relation(session, user.id, relation_id)
    return success(data, "关系已删除")
