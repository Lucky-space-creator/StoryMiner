"""
知识图谱路由（M5）

整体思路：
    暴露 M5 接口：图谱查询（按小说组装节点/边）、实体关系抽取（后台任务）、关系类型字典。
    图谱与抽取挂在 /novels 前缀下（与小说资源同域），关系类型字典独立暴露。

关键点：
    1. 所有写接口依赖 get_current_user 取得 owner_id，实现数据隔离。
    2. 抽取为耗时操作，使用 BackgroundTasks 后台执行，接口立即返回已触发状态。
    3. 响应统一 {code,msg,data}。

实现逻辑：
    委托 graph_service；抽取后台任务自开 SessionLocal 落库，避免阻塞请求。
"""
from fastapi import APIRouter, BackgroundTasks, Depends

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


@router.get("/{novel_id}/graph")
async def get_graph(
    novel_id: int,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """知识图谱查询（M5.1）：组装节点/关系/类别供前端 ECharts 渲染。"""
    graph = await graph_service.get_graph(session, novel_id, user.id)
    return success(graph)


@router.post("/{novel_id}/extract")
async def extract_graph(
    novel_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """实体关系抽取（M5.2）：后台调用 LLM 抽取并落库，接口立即返回统一 task_id。"""
    novel = await novel_repo.get_novel(session, user.id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")
    task = await task_service.create_task(session, user.id, "graph", f"小说{novel.name}-知识图谱抽取", novel_id=novel_id)
    task_queue.submit(graph_service.run_extract, novel_id, user.id, task.id)
    return success({"task_id": task.id}, "已启动实体关系抽取")


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
