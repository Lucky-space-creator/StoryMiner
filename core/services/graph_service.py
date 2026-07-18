"""
知识图谱业务逻辑（M5 实体关系抽取与图谱组装）

整体思路：
    封装两类能力：(1) 图谱查询，将实体/关系组装为前端 ECharts 所需的
    {nodes, links, categories} 结构；(2) 实体关系抽取，调用本地 Ollama 大模型
    从章节文本中抽取实体与关系并幂等落库。

关键点：
    1. 实体按 (novel_id, name, type) 去重；关系按 (source_id, target_id, type) 去重，重复抽取不翻倍。
    2. 抽取为耗时操作，run_extract 作为后台任务自开会话落库，避免阻塞 HTTP 请求。
    3. 大模型输出做容错解析（去 markdown 包裹、截取 JSON 片段），失败不中断整体流程。

实现逻辑：
    get_graph 批量取实体/关系并按度数计算节点权重；extract 拼文本→调 chat→解析→upsert。
"""
import json

from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from models.graph import Entity, Relation
from repositories import graph_repo, novel_repo, llm_repo
from services import llm_adapters, task_service
from common import crypto
from common.exceptions import BizError

# 实体类型 → 中文类别（前端图例/着色用）
_TYPE_CN = {"character": "人物", "place": "地点", "org": "组织"}

# 抽取提示词模板：约束实体类型与 JSON 输出格式
_PROMPT = """你是一个小说知识图谱抽取引擎，请从给定文本中抽取实体与关系。
实体类型仅限：character(人物)、place(地点)、org(组织)。
关系类型自由发挥，使用中文短语描述两者关系（如：师徒、父子、夫妻、朋友、敌对、主仆、爱慕、同门、上下级、亲属）。
仅输出一个 JSON 对象，不要包含任何解释或 markdown 标记，格式严格如下：
{
  "entities": [{"name":"张三","type":"character","profile":{"身份":"主角","性格":"坚毅"}}],
  "relations": [{"source":"张三","target":"李四","type":"师徒","evidence":"张三拜李四为师"}]
}
待抽取文本：
{text}"""


async def _get_chat_adapter(session: AsyncSession, owner_id: int):
    """取默认对话模型适配器（M9 分发链），返回 (adapter, model_name)。"""
    cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
    if not cfgs:
        raise BizError(400, "尚未配置对话模型（llm_type=chat），请先在模型管理中添加")
    cfg = cfgs[0]
    return llm_adapters.get_adapter(cfg, crypto.decrypt(cfg.api_key)), cfg.model


async def get_graph(session: AsyncSession, novel_id: int, owner_id: int) -> dict:
    """图谱查询（M5.1）：组装 ECharts 所需的节点/边/类别。

    实现逻辑：批量取实体与关系，按度数计算节点权重，实体类型映射中文类别。
    """
    entities = await graph_repo.list_entities(session, novel_id)
    relations = await graph_repo.list_relations(session, novel_id)
    degree: dict[int, int] = {}
    for r in relations:
        degree[r.source_id] = degree.get(r.source_id, 0) + 1
        degree[r.target_id] = degree.get(r.target_id, 0) + 1
    categories: list[str] = []
    nodes = []
    for e in entities:
        cat = _TYPE_CN.get(e.type, e.type)
        if cat not in categories:
            categories.append(cat)
        nodes.append({
            "id": e.id, "name": e.name, "category": cat,
            "value": degree.get(e.id, 0), "type": e.type,
        })
    links = [{"source": r.source_id, "target": r.target_id, "label": r.type} for r in relations]
    return {"nodes": nodes, "links": links, "categories": categories}


async def list_relation_types(session: AsyncSession, owner_id: int) -> list:
    """关系类型字典（M5.3）：系统内置 + 本人自定义。"""
    return await graph_repo.list_relation_types(session, owner_id)


async def extract(session: AsyncSession, novel_id: int, owner_id: int) -> dict:
    """实体关系抽取（M5.2）：LLM 抽取并幂等落库，返回新增统计。"""
    chapters = await novel_repo.list_all_chapters(session, novel_id)
    if not chapters:
        raise BizError(400, "该小说暂无章节，无法抽取")
    text = "\n".join(c.content for c in chapters)[:12000]
    adapter, _ = await _get_chat_adapter(session, owner_id)
    raw = await adapter.chat([{"role": "user", "content": _PROMPT.replace("{text}", text)}])
    data = _parse_json(raw)

    ent_count = 0
    for ed in data.get("entities", []):
        name = (ed.get("name") or "").strip()
        if not name:
            continue
        etype = ed.get("type", "character") or "character"
        if etype not in ("character", "place", "org"):
            etype = "character"
        if await graph_repo.get_entity(session, novel_id, name, etype):
            continue
        await graph_repo.create_entity(session, Entity(
            novel_id=novel_id, owner_id=owner_id, name=name, type=etype,
            profile=ed.get("profile") or {}, description=ed.get("description"),
        ))
        ent_count += 1

    ents = await graph_repo.list_entities(session, novel_id)
    emap = {(e.name, e.type): e.id for e in ents}
    rel_count = 0
    for rd in data.get("relations", []):
        sname, tname, rtype = (rd.get("source") or "").strip(), (rd.get("target") or "").strip(), (rd.get("type") or "").strip()
        if not (sname and tname and rtype):
            continue
        sid = emap.get((sname, "character")) or emap.get((sname, "place")) or emap.get((sname, "org"))
        tid = emap.get((tname, "character")) or emap.get((tname, "place")) or emap.get((tname, "org"))
        if not (sid and tid):
            continue
        if await graph_repo.get_relation(session, sid, tid, rtype):
            continue
        await graph_repo.create_relation(session, Relation(
            novel_id=novel_id, owner_id=owner_id, source_id=sid, target_id=tid,
            type=rtype, evidence=rd.get("evidence"),
        ))
        rel_count += 1

    await session.commit()
    return {"entity_count": ent_count, "relation_count": rel_count}


async def run_extract(novel_id: int, owner_id: int, task_id: int | None = None) -> None:
    """后台抽取入口：自开会话调用 extract，进度回写统一任务，异常仅记录不抛出。"""
    from db import SessionLocal
    async with SessionLocal() as session:
        try:
            if task_id:
                await task_service.update_task_progress(task_id, stage="extracting", progress=20, status="running", started_at=datetime.now(timezone.utc))
            result = await extract(session, novel_id, owner_id)
            if task_id:
                await task_service.update_task_progress(
                    task_id, stage="done", progress=100, status="success", finished_at=datetime.now(timezone.utc),
                    entity_count=result.get("entity_count", 0), relation_count=result.get("relation_count", 0),
                )
            print(f"[graph] 抽取完成 novel={novel_id} {result}")
        except Exception as e:  # 后台任务异常不应影响主流程
            if task_id:
                await task_service.update_task_progress(task_id, stage="failed", status="failed", error=str(e), finished_at=datetime.now(timezone.utc))
            print(f"[graph] 抽取失败 novel={novel_id}: {e}")


def _parse_json(raw: str) -> dict:
    """容错解析 LLM 输出的 JSON（去 markdown 包裹、截取首尾花括号）。"""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start:end + 1]
    try:
        return json.loads(raw)
    except Exception:
        return {}
