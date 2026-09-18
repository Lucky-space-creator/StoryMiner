"""
知识图谱业务逻辑（M5 实体关系抽取与图谱组装）

整体思路：
    封装两类能力：(1) 图谱查询，将实体/关系组装为前端 ECharts 所需的
    {nodes, links, categories} 结构；(2) 实体关系抽取，调用 LLM 从章节文本中
    抽取7种实体类型与关系，支持分块抽取与先删后建策略。

关键点：
    1. V13 扩展实体类型为7种：character(人物)/place(地点)/org(组织)/
       time_period(时间)/event(事件)/item(物品)/concept(概念)。
    2. 抽取采用「先删后建」策略：每次抽取前物理删除该小说全部现有实体与关系，
       然后重新创建导入，避免旧数据污染。
    3. 大文本采用分块策略：超过8000字符时按块切分，逐块抽取并合并去重。
    4. run_extract 作为后台任务自开会话落库，进度分阶段回写。

实现逻辑：
    get_graph 批量取实体/关系并按度数计算节点权重；
    extract 先删后建→分块拼文本→逐块调 chat→合并解析→批量 upsert 落库。
"""
import asyncio
import json
import os
import re

from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from models.graph import Entity, Relation
from repositories import graph_repo, novel_repo, llm_repo
from services import task_service
from common import crypto
from common import task_cancel
from common import nlp
from common.exceptions import BizError

# ───────────────────────────── 实体类型定义（V13 扩展为7种） ─────────────────────────────

# 实体类型 → 中文类别（前端图例/着色用）
_TYPE_CN = {
    "character": "人物",
    "place": "地点",
    "org": "组织",
    "time_period": "时间",
    "event": "事件",
    "item": "物品",
    "concept": "概念",
}

# 有效的实体类型白名单
_VALID_ENTITY_TYPES = frozenset(_TYPE_CN.keys())

# 实体类型匹配优先级（确定性）：同名实体跨多类型存在时，关系连接优先按此序取值，
# 避免 frozenset 迭代顺序不确定导致关系指向随机类型（P2-12）。人物(character)优先。
_ENTITY_TYPE_PRIORITY = tuple(_TYPE_CN.keys())

# 实体类型默认颜色（与 story_entity_type 种子数据一致）
_TYPE_COLORS = {
    "character": "#0d9488",
    "place": "#6366f1",
    "org": "#d97706",
    "time_period": "#8b5cf6",
    "event": "#dc2626",
    "item": "#16a34a",
    "concept": "#0891b2",
}

# 分块抽取参数
_CHUNK_SIZE = 8000     # 每块最大字符数
_CHUNK_OVERLAP = 500   # 块间重叠字符数

# ───────────────────────────── 抽取 Prompt（V13 重设计：7种实体类型 + 详细约束） ─────────────────────────────

_EXTRACT_SYSTEM = """你是一个专业的小说知识图谱抽取引擎，请严格按以下规范从文本中抽取实体与关系。

## 实体类型定义（共7种，必须使用指定 code）

1. **character（人物）**：小说中登场的人物角色，含主角、配角、龙套、提到名字但未出场的历史/传说人物
2. **place（地点）**：场景、地理位置、建筑、城池、国家、区域、洞府、秘境
3. **org（组织）**：帮派、宗门、势力、家族、王朝、机构、队伍
4. **time_period（时间）**：具体时间点、时间段、历史时期、朝代纪年、人物年龄、事件持续时长
5. **event（事件）**：发生的重要事件、情节转折、战斗、会议、仪式、比武、突破、死亡
6. **item（物品）**：重要道具、武器、法宝、丹药、秘籍、信物、货币、材料
7. **concept（概念）**：功法体系、修炼境界、世界观设定、特殊规则、文化概念、种族

## 抽取规范
- name 必须使用原文中的准确名称，不要杜撰
- profile 为 JSON 对象，记录关键属性（如：{"身份":"主角","性别":"男","境界":"筑基期"}）
- description 用一句简短中文概括该实体在原文中的作用
- 每个实体至少要有一个有意义的关系连接，孤立节点可忽略

## 输出格式
严格输出一个 JSON 对象，不要包含任何解释或 markdown 标记：
{
  "entities": [
    {"name":"张三","type":"character","profile":{"身份":"主角"},"description":"小说主人公"},
    {"name":"天剑宗","type":"org","profile":{"类型":"宗门"},"description":"主角所在宗门"}
  ],
  "relations": [
    {"source":"张三","target":"天剑宗","type":"所属宗门","evidence":"原文证据句"}
  ]
}"""

_EXTRACT_USER = "请从以下小说文本中抽取所有实体和关系（共7种类型：character/place/org/time_period/event/item/concept）：\n\n{text}"

# ───────────────────────────── M6 混合管道：实体/关系分离抽取 ─────────────────────────────
# 实体仍由 LLM 分块抽取（7 类），但关系不再随实体全量抽，而是：
# 先 nlp 共现出候选边（确定性）→ 仅对候选边调 LLM 定性（类型/方向/证据），
# 调用次数 = 候选边数（远少于分块数），落实「调用降」。

# 实体抽取提示（仅实体，不含关系输出，省 token）
_ENTITY_SYSTEM = """你是小说知识图谱实体抽取引擎，请严格按规范从文本抽取7种实体类型。

## 实体类型（共7种，必须使用指定 code）
1. character(人物) 2. place(地点) 3. org(组织) 4. time_period(时间)
5. event(事件) 6. item(物品) 7. concept(概念)

## 规范
- name 用原文准确名称，勿杜撰
- profile 为 JSON 对象记录关键属性（如 {"身份":"主角","境界":"筑基期"}）
- description 用一句简短中文概括该实体在原文中的作用
- 仅抽取实体，不要输出关系（关系将另由共现分析定性）

## 输出格式（严格 JSON，无 markdown、无解释）
{"entities":[{"name":"张三","type":"character","profile":{"身份":"主角"},"description":"小说主人公"}]}"""

_ENTITY_USER = """请从以下小说文本抽取全部实体（7种类型）。
下列是由确定性预分析给出的候选实体（仅供参考，请核实原文后抽取，勿漏抽其他实体）：
{candidates}

文本：
{text}"""

# 关系定性提示（每条候选边一次 LLM 调用）
_REL_QUAL_SYSTEM = """你是小说关系定性助手。给定两个频繁共现的实体 A 与 B 及原文片段，判断二者关系。
仅输出一个 JSON 对象，不要 markdown、不要解释：
{"type":"关系类型","direction":"A->B"|"B->A"|"none","evidence":"一句原文证据"}
- type 用简洁中文（如 师徒/敌对/同门/父子/主仆/夫妻/上下级/挚友/仇敌）
- direction：若关系有方向（如 A 是 B 的师父）则 A->B；反之 B->A；无向则 none"""

_REL_QUAL_USER = """小说中「{a}」与「{b}」频繁共同出现。请基于以下原文片段判断二者关系：
{ctx}"""

# 共现候选边取 TopK 参与 LLM 定性（控制调用次数，残脉逆仙约260边→取前60）
_COOCCUR_TOPK = 60

# P4：分块抽取并发度（模块级单例信号量，不可放入函数内 —— 每次新建等于不限流）。
# 取值参考：本地 Ollama 建议 1~2；云端 API 按其 RPM 限额换算。
# 若启用 Celery 多 worker，总并发 = worker 数 × 该值，需相应下调。
_EXTRACT_SEM = asyncio.Semaphore(int(os.getenv("GRAPH_EXTRACT_CONCURRENCY", "4")))


def _format_candidates(cands: dict) -> str:
    """把 nlp.extract_named_entities 候选格式化为注入文本。"""
    parts = []
    for t in ("character", "place", "org", "time"):
        names = [c["name"] for c in cands.get(t, [])]
        if names:
            parts.append(f"{t}: " + "、".join(names))
    return "\n".join(parts) if parts else "（无）"


def _pair_context(text: str, a: str, b: str, max_chars: int = 3000) -> str:
    """取 a 与 b 共现的窗口片段（关系定性喂料），截断到 max_chars。"""
    import re as _re
    idxs = [m.start() for m in _re.finditer(_re.escape(a), text)]
    out, total = [], 0
    for i in idxs:
        s, e = max(0, i - 150), min(len(text), i + 300)
        snippet = text[s:e]
        if b in snippet:
            out.append(snippet)
            total += (e - s)
            if total >= max_chars:
                break
    if not out:
        if idxs:
            i = idxs[0]
            return text[max(0, i - 150):min(len(text), i + 300)]
        return text[:max_chars]
    return "\n……\n".join(out)[:max_chars]


async def _acall_llm(messages, *, cfg):
    """经 LangChain 调用层发起 LLM 调用，返回 (文本, usage_dict)。
    usage 由调用方累积，任务结束时由 update_task_progress 统一写入，不逐次写 DB。
    """
    from llm.langchain_factory import get_langchain_model, invoke_with_usage
    model = get_langchain_model(cfg)
    resp, usage = await invoke_with_usage(model, messages)
    content = getattr(resp, "content", None)
    return (content if isinstance(content, str) else str(resp), usage)


async def _qualify_relations(text: str, topk: list[dict], *, cfg, config_id: int,
                            owner_id: int, task_id: int | None = None) -> tuple[list[dict], int, int, int]:
    """M6 关系定性核心：对共现候选边逐条 LLM 定性（类型/方向/证据）。

    返回 (all_relations, rel_calls, tok_in, tok_out)；调用次数 = len(topk)（远少于分块数），
    落实「调用降 ≥50%」。direction 决定 source/target 顺序，none 则保持原序。
    token 用量由调用方累积，任务结束时统一写入 DB。
    """
    all_relations: list[dict] = []
    rel_calls = 0
    tok_in = 0
    tok_out = 0
    for i, edge in enumerate(topk):
        if task_id and task_cancel.is_cancelled(task_id):
            raise task_cancel.TaskCancelled("用户主动取消任务", tok_in, tok_out)
        if task_id:
            await task_service.update_task_progress(
                task_id, stage="qualifying_relations",
                progress=88 + int(i / max(len(topk), 1) * 7), status="running")
        ctx = _pair_context(text, edge["source"], edge["target"])
        prompt = (_REL_QUAL_USER.replace("{a}", edge["source"])
                  .replace("{b}", edge["target"]).replace("{ctx}", ctx))
        messages = [{"role": "system", "content": _REL_QUAL_SYSTEM},
                    {"role": "user", "content": prompt}]
        raw, usage = await _acall_llm(messages, cfg=cfg)
        if usage:
            tok_in += usage.get("tokens_in", 0)
            tok_out += usage.get("tokens_out", 0)
        data = _parse_json(raw)
        if not isinstance(data, dict):
            continue
        rtype = (data.get("type") or "").strip()
        if not rtype:
            continue
        direction = data.get("direction") or "none"
        sname, tname = (edge["target"], edge["source"]) if direction == "B->A" \
            else (edge["source"], edge["target"])
        all_relations.append({"source": sname, "target": tname,
                              "type": rtype, "evidence": data.get("evidence")})
        rel_calls += 1
    return all_relations, rel_calls, tok_in, tok_out


# ───────────────────────────── LLM 适配器 ─────────────────────────────

async def _get_chat_adapter(session: AsyncSession, owner_id: int):
    """取默认对话模型配置（M9 分发链），返回 (cfg, model_name)。

    返回默认对话模型配置信息（供 LangChain 工厂内部构建模型）；cfg 透传给 M6 关系定性 LLM 调用。
    """
    cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
    if not cfgs:
        raise BizError(400, "尚未配置对话模型（llm_type=chat），请先在模型管理中添加")
    cfg = cfgs[0]
    return cfg, cfg.model


# ───────────────────────────── JSON 解析 ─────────────────────────────

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


def _normalize_type(etype: str) -> str:
    """规范化实体类型：不在白名单内的默认归为 character。"""
    etype = (etype or "character").strip().lower()
    if etype not in _VALID_ENTITY_TYPES:
        # 尝试模糊匹配
        fuzzy_map = {
            "person": "character", "people": "character", "人": "character", "角色": "character",
            "location": "place", "位置": "place", "场景": "place",
            "organization": "org", "组织": "org", "势力": "org", "帮派": "org",
            "time": "time_period", "时间": "time_period", "时期": "time_period", "时代": "time_period",
            "事件": "event", "情节": "event",
            "object": "item", "物品": "item", "道具": "item", "武器": "item",
            "概念": "concept", "设定": "concept",
        }
        etype = fuzzy_map.get(etype, "character")
    return etype


# ───────────────────────────── 文本分块 ─────────────────────────────

def _split_text(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """将长文本按指定大小分块，块间保留重叠区域以防止边界实体被切断。

    关键点：在句子边界（句号/问号/感叹号/换行）处切割，避免在词语中间断开。
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            # 在句子边界处切割
            boundary = max(text.rfind("。", start, end), text.rfind("\n", start, end),
                           text.rfind("？", start, end), text.rfind("！", start, end))
            if boundary > start + chunk_size // 2:
                end = boundary + 1
        chunks.append(text[start:end])
        start = end - overlap if end < len(text) else len(text)
    return chunks


# ───────────────────────────── 核心图谱查询 ─────────────────────────────

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
    categories: list[dict] = []
    cat_set: set[str] = set()
    nodes = []
    name_by_id: dict[int, str] = {}
    for e in entities:
        cat = _TYPE_CN.get(e.type, e.type)
        if cat not in cat_set:
            cat_set.add(cat)
            categories.append({
                "name": cat,
                "code": e.type,
                "color": _TYPE_COLORS.get(e.type, "#888888"),
            })
        name_by_id[e.id] = e.name
        nodes.append({
            "id": e.id, "name": e.name, "category": cat,
            "value": degree.get(e.id, 0), "type": e.type,
            "profile": e.profile, "description": e.description,
        })
    links = [{
        "id": r.id, "source": r.source_id, "target": r.target_id, "label": r.type,
        "evidence": r.evidence, "sourceName": name_by_id.get(r.source_id, ""),
        "targetName": name_by_id.get(r.target_id, ""),
    } for r in relations]
    return {"nodes": nodes, "links": links, "categories": categories}


async def check_graph_exists(session: AsyncSession, novel_id: int) -> dict:
    """检测小说是否已有图谱数据（V13 新增，供前端抽取前确认）。"""
    ent_count, rel_count = await graph_repo.count_entities_by_novel(session, novel_id)
    return {
        "has_entities": ent_count > 0,
        "entity_count": ent_count,
        "relation_count": rel_count,
    }


async def list_relation_types(session: AsyncSession, owner_id: int) -> list:
    """关系类型字典（M5.3）：系统内置 + 本人自定义。"""
    return await graph_repo.list_relation_types(session, owner_id)


async def list_entity_types(session: AsyncSession) -> list:
    """实体类型字典（V13 新增）：返回7种内置类型及其颜色/符号。"""
    return await graph_repo.list_entity_types(session)


# ───────────────────────────── 核心抽取：先删后建 + 分块抽取（V13 重写） ─────────────────────────────

async def clear_graph(session: AsyncSession, novel_id: int) -> int:
    """清空小说图谱数据：物理删除所有实体（级联关系）。

    关键点：先删关系再删实体，避免外键约束报错。
    """
    return await graph_repo.delete_entities_by_novel(session, novel_id)


async def extract(session: AsyncSession, novel_id: int, owner_id: int, task_id: int | None = None) -> dict:
    """实体关系抽取（V13 重写）：先删后建 + 分块抽取 + 7种实体类型。

    整体思路：
        1. 先清空该小说全部现有实体和关系
        2. 获取全部章节文本
        3. 大文本按8000字符分块，块间重叠500字符
        4. 逐块调用 LLM 抽取，合并所有结果
        5. 全局去重后批量落库

    关键点：
        1. 先删后建确保不残留旧数据
        2. 分块策略避免长文本超出 LLM 上下文窗口
        3. 跨块实体按 (name, type) 全局去重
        4. 事件实体的 profile 中记录参与者与时间信息
        5. token 用量累积至末尾，由 run_extract 统一写入 DB，不逐次写
    """
    # ── 局部 token 累积（全过程增量），取消时一并上报 ──
    tok_in = 0
    tok_out = 0
    # ---------- Step 1: 清空现有数据 ----------
    deleted = await graph_repo.delete_entities_by_novel(session, novel_id)
    print(f"[graph] 已清空 novel={novel_id} 的 {deleted} 个旧实体及其关系")

    # ---------- Step 2: 获取章节文本 ----------
    chapters = await novel_repo.list_all_chapters(session, novel_id)
    if not chapters:
        raise BizError(400, "该小说暂无章节，无法抽取")
    text = "\n".join(c.content for c in chapters)
    total_chars = len(text)
    print(f"[graph] 小说 novel={novel_id} 全文 {total_chars} 字符")

    # ---------- Step 3: 分块 ----------
    chunks = _split_text(text)
    chunk_count = len(chunks)
    print(f"[graph] 分为 {chunk_count} 块，每块约 {_CHUNK_SIZE} 字符")

    # ---------- Step 4: 获取 LLM 配置 ----------
    cfg, model_name = await _get_chat_adapter(session, owner_id)
    config_id = cfg.id
    print(f"[graph] 使用模型: {model_name}")

    # ---------- Step 5: 逐块抽取 ----------
    all_entities: list[dict] = []
    all_relations: list[dict] = []
    entity_set: set[tuple[str, str]] = set()  # (name, type) 全局去重

    # 确定性候选（零成本）：供实体抽取提示与关系定性边入围（M6）
    cand_block = _format_candidates(nlp.extract_named_entities(text))
    print(f"[graph] 确定性候选实体已产出（供 LLM 参考）")

    # P4：分块抽取改为并发执行（原为串行 for 循环）。
    # 并发度由 _EXTRACT_CONCURRENCY 控制（受 LLM 限流约束），
    # 收益：N 块的总耗时从 N×t 降至 ceil(N/并发)×t。
    # 关键点：分块之间无依赖（仅共享只读的 cand_block），故可安全并发；
    #        合并去重在并发收敛后单点进行（避免并发写集合的竞态）。
    async def extract_chunk(idx: int, chunk: str) -> tuple[int, list[dict], int, int]:
        """抽取单块的实体：返回 (块序号, 实体列表, tok_in, tok_out)。

        关键点：单块异常只影响该块（返回空结果），不阻断其他块。
        """
        if task_id and task_cancel.is_cancelled(task_id):
            raise task_cancel.TaskCancelled("用户主动取消任务", 0, 0)
        messages = [
            {"role": "system", "content": _ENTITY_SYSTEM},
            {"role": "user", "content": _ENTITY_USER.replace(
                "{candidates}", cand_block).replace("{text}", chunk)},
        ]
        try:
            async with _EXTRACT_SEM:              # 模块级信号量限流
                raw, usage = await _acall_llm(messages, cfg=cfg)
        except task_cancel.TaskCancelled:
            raise
        except Exception as e:                    # noqa: BLE001
            print(f"[graph] 第{idx}/{chunk_count}块抽取失败，已跳过：{e}")
            return idx, [], 0, 0

        u_in = (usage or {}).get("tokens_in", 0)
        u_out = (usage or {}).get("tokens_out", 0)
        data = _parse_json(raw)
        ents = []
        for ed in data.get("entities", []):
            name = (ed.get("name") or "").strip()
            if not name:
                continue
            ents.append({
                "name": name, "type": _normalize_type(ed.get("type", "character")),
                "profile": ed.get("profile") or {},
                "description": ed.get("description"),
            })
        return idx, ents, u_in, u_out

    if task_id:
        await task_service.update_task_progress(
            task_id, stage=f"extracting_0/{chunk_count}",
            progress=20, status="running")

    # 并发抽取所有块（return_exceptions=False：取消异常需向上抛以终止任务）
    results = await asyncio.gather(
        *[extract_chunk(i, c) for i, c in enumerate(chunks, 1)])

    # 收敛后单点合并去重（(name, type) 全局去重），避免并发写竞态
    for _idx, ents, u_in, u_out in results:
        tok_in += u_in
        tok_out += u_out
        for ed in ents:
            key = (ed["name"], ed["type"])
            if key not in entity_set:
                entity_set.add(key)
                all_entities.append(ed)

    if task_id:
        await task_service.update_task_progress(
            task_id, stage=f"extracted_{chunk_count}/{chunk_count}",
            progress=85, status="running", tokens_in=tok_in, tokens_out=tok_out)
    print(f"[graph] 分块抽取完成: {chunk_count} 块 → 去重后实体 {len(all_entities)} 个")

    # ---------- Step 6: 批量落库实体 ----------
    for ed in all_entities:
        await graph_repo.create_entity(session, Entity(
            novel_id=novel_id, owner_id=owner_id,
            name=ed["name"], type=ed["type"],
            profile=ed["profile"], description=ed["description"],
        ))
    await session.flush()

    # 构建 (name, type) → id 映射
    ents = await graph_repo.list_entities(session, novel_id)
    emap: dict[tuple[str, str], int] = {(e.name, e.type): e.id for e in ents}

    # ---------- Step 7（M6 混合管道）：关系定性（共现候选边 → LLM 仅定性） ----------
    co_edges = nlp.build_cooccurrence(text)
    co_edges.sort(key=lambda e: e.get("weight", 0), reverse=True)
    topk = co_edges[:_COOCCUR_TOPK]
    all_relations, rel_calls, rel_tok_in, rel_tok_out = await _qualify_relations(
        text, topk, cfg=cfg, config_id=config_id, owner_id=owner_id, task_id=task_id)
    tok_in += rel_tok_in
    tok_out += rel_tok_out
    print(f"[graph] 关系定性: 候选边 {len(topk)} 条 → 有效关系 {rel_calls} 条 "
          f"（分块抽取 {chunk_count} 次 → 关系调用降至 {rel_calls} 次）")

    # ---------- Step 8: 批量落库关系 ----------
    # 实体类型匹配优先级（确定性）：同名实体若跨多类型存在，优先 character → 其余按既定顺序，
    # 消除「遍历 _VALID_ENTITY_TYPES 顺序不确定导致关系指向随机类型」的非确定性（P2-12）。
    _TYPE_ORDER = sorted(_VALID_ENTITY_TYPES, key=lambda t: _ENTITY_TYPE_PRIORITY.index(t)
                         if t in _ENTITY_TYPE_PRIORITY else len(_ENTITY_TYPE_PRIORITY))
    rel_set: set[tuple[int, int, str]] = set()  # (sid, tid, type) 去重
    rel_count = 0
    for rd in all_relations:
        # 优先用关系自带的类型提示（若 LLM 返回 source_type/target_type）精确匹配，否则按优先级
        s_hint = rd.get("source_type")
        t_hint = rd.get("target_type")
        sid = emap.get((rd["source"], s_hint)) if s_hint else None
        tid = emap.get((rd["target"], t_hint)) if t_hint else None
        if sid is None:
            for etype in _TYPE_ORDER:
                sid = emap.get((rd["source"], etype))
                if sid:
                    break
        if tid is None:
            for etype in _TYPE_ORDER:
                tid = emap.get((rd["target"], etype))
                if tid:
                    break
        if not (sid and tid):
            continue
        key = (sid, tid, rd["type"])
        if key in rel_set:
            continue
        rel_set.add(key)
        await graph_repo.create_relation(session, Relation(
            novel_id=novel_id, owner_id=owner_id,
            source_id=sid, target_id=tid,
            type=rd["type"], evidence=rd.get("evidence"),
        ))
        rel_count += 1

    await session.commit()
    result = {
        "entity_count": len(all_entities), "relation_count": rel_count, "chunks": chunk_count,
        "tokens_in": tok_in, "tokens_out": tok_out,
        "config_id": config_id, "model": model_name,
    }
    print(f"[graph] 抽取完成 novel={novel_id}: {result}")
    return result


# ───────────────────────────── 后台抽取入口 ─────────────────────────────

async def run_extract(novel_id: int, owner_id: int, task_id: int | None = None) -> None:
    """后台抽取入口：自开会话调用 extract，进度回写统一任务，异常仅记录不抛出。

    整体思路：
        1. 自建独立 SessionLocal 避免阻塞HTTP请求
        2. 分阶段更新任务进度：pending → clearing → extracting_N/M → done
        3. 支持任务取消检查
    """
    from db import SessionLocal
    async with SessionLocal() as session:
        # 预初始化 token/usage 变量，确保 except 分支引用不会 NameError
        _tokens_in = 0
        _tokens_out = 0
        _usage_info = None
        try:
            if task_id:
                if task_cancel.is_cancelled(task_id):
                    await task_service.update_task_progress(
                        task_id, stage="cancelled", status="cancelled",
                        error="用户主动取消任务", finished_at=datetime.now(timezone.utc))
                    return
                await task_service.update_task_progress(
                    task_id, stage="clearing", progress=5, status="running",
                    started_at=datetime.now(timezone.utc))

            result = await extract(session, novel_id, owner_id, task_id)
            # 提取 token 用量和 LLM 配置信息，供终态统一写入
            _tokens_in = result.get("tokens_in", 0)
            _tokens_out = result.get("tokens_out", 0)
            _usage_info = {"config_id": result.get("config_id"), "model": result.get("model"), "task_type": "graph_extract"}

            if task_id:
                if task_cancel.is_cancelled(task_id):
                    await task_service.update_task_progress(
                        task_id, stage="cancelled", status="cancelled",
                        error="用户主动取消任务", finished_at=datetime.now(timezone.utc),
                        tokens_in=_tokens_in, tokens_out=_tokens_out, usage_info=_usage_info)
                    return
                await task_service.update_task_progress(
                    task_id, stage="done", progress=100, status="success",
                    finished_at=datetime.now(timezone.utc),
                    entity_count=result.get("entity_count", 0),
                    relation_count=result.get("relation_count", 0),
                    tokens_in=_tokens_in, tokens_out=_tokens_out, usage_info=_usage_info,
                )
            print(f"[graph] 抽取完成 novel={novel_id} {result}")
        except Exception as e:
            if task_id:
                if isinstance(e, task_cancel.TaskCancelled):
                    # 获取异常中已累积的 token + config 信息（extract 内 _qualify_relations 取消时会携带）
                    ct_in = getattr(e, "tokens_in", 0) or 0
                    ct_out = getattr(e, "tokens_out", 0) or 0
                    # 从正常流程累积的 _usage_info 取（已在函数开头初始化为 None，无需 dir() 探测）
                    cu = _usage_info
                    await task_service.update_task_progress(
                        task_id, stage="cancelled", status="cancelled",
                        error=e.reason, finished_at=datetime.now(timezone.utc),
                        tokens_in=ct_in, tokens_out=ct_out, usage_info=cu)
                else:
                    # 普通失败：同样持久化已累积的 token 用量与配置，确保门户能看到失败任务的用量统计
                    # （_tokens_in/_tokens_out/_usage_info 在 try 块 extract 返回后已赋值）
                    await task_service.update_task_progress(
                        task_id, stage="failed", status="failed",
                        error=str(e), finished_at=datetime.now(timezone.utc),
                        tokens_in=_tokens_in, tokens_out=_tokens_out, usage_info=_usage_info)
            print(f"[graph] 抽取失败 novel={novel_id}: {e}")


# ───────────────────────────── 实体/关系 CRUD（M5 增强） ─────────────────────────────

_ENTITY_TYPES = tuple(_VALID_ENTITY_TYPES)


async def create_entity(session: AsyncSession, novel_id: int, owner_id: int, payload: dict) -> dict:
    """新增实体：校验名称/类型/唯一性，落库并返回概要。"""
    name = (payload.get("name") or "").strip()
    if not name:
        raise BizError(400, "实体名称不能为空")
    etype = _normalize_type(payload.get("type", "character"))
    if await graph_repo.get_entity(session, novel_id, name, etype):
        raise BizError(409, "该实体已存在")
    e = await graph_repo.create_entity(session, Entity(
        novel_id=novel_id, owner_id=owner_id, name=name, type=etype,
        profile=payload.get("profile") or {}, description=payload.get("description"),
    ))
    await session.commit()
    return {"id": e.id, "name": e.name, "type": e.type}


async def update_entity(session: AsyncSession, owner_id: int, entity_id: int, payload: dict) -> dict:
    """修改实体：按需更新名称/类型/画像/描述，校验唯一冲突。"""
    e = await graph_repo.get_entity_by_id(session, entity_id)
    if not e or e.owner_id != owner_id:
        raise BizError(404, "实体不存在")
    name = (payload.get("name") or "").strip()
    etype = _normalize_type(payload.get("type", e.type))
    if name and (name != e.name or etype != e.type):
        if await graph_repo.get_entity(session, e.novel_id, name, etype):
            raise BizError(409, "该实体已存在")
        e.name = name
    e.type = etype
    if "profile" in payload:
        e.profile = payload.get("profile") or {}
    if "description" in payload:
        e.description = payload.get("description")
    await session.commit()
    return {"id": e.id, "name": e.name, "type": e.type}


async def delete_entity(session: AsyncSession, owner_id: int, entity_id: int) -> dict:
    """物理删除实体，并级联删除其关联的关系，避免外键悬空。"""
    e = await graph_repo.get_entity_by_id(session, entity_id)
    if not e or e.owner_id != owner_id:
        raise BizError(404, "实体不存在")
    rels = await graph_repo.list_relations(session, e.novel_id)
    for r in rels:
        if r.source_id == entity_id or r.target_id == entity_id:
            await graph_repo.delete_relation(session, r)
    await graph_repo.delete_entity(session, e)
    await session.commit()
    return {"deleted": entity_id}


async def create_relation(session: AsyncSession, novel_id: int, owner_id: int, payload: dict) -> dict:
    """新增关系：校验端点存在、类型非空、非自环、唯一性。"""
    sid = payload.get("source_id")
    tid = payload.get("target_id")
    rtype = (payload.get("type") or "").strip()
    if not (sid and tid and rtype):
        raise BizError(400, "关系需包含 source_id/target_id/type")
    if sid == tid:
        raise BizError(400, "关系起点与终点不能相同")
    src = await graph_repo.get_entity_by_id(session, sid)
    tgt = await graph_repo.get_entity_by_id(session, tid)
    if not src or src.novel_id != novel_id or not tgt or tgt.novel_id != novel_id:
        raise BizError(400, "起点或终点实体不存在")
    if await graph_repo.get_relation(session, sid, tid, rtype):
        raise BizError(409, "该关系已存在")
    r = await graph_repo.create_relation(session, Relation(
        novel_id=novel_id, owner_id=owner_id, source_id=sid, target_id=tid,
        type=rtype, evidence=payload.get("evidence"),
    ))
    await session.commit()
    return {"id": r.id, "source_id": r.source_id, "target_id": r.target_id, "type": r.type}


async def update_relation(session: AsyncSession, owner_id: int, relation_id: int, payload: dict) -> dict:
    """修改关系：可改类型/出处证据/端点，校验端点合法性。"""
    r = await graph_repo.get_relation_by_id(session, relation_id)
    if not r or r.owner_id != owner_id:
        raise BizError(404, "关系不存在")
    if payload.get("type"):
        r.type = (payload.get("type") or "").strip()
    if "evidence" in payload:
        r.evidence = payload.get("evidence")
    if "source_id" in payload or "target_id" in payload:
        sid = payload.get("source_id", r.source_id)
        tid = payload.get("target_id", r.target_id)
        if sid == tid:
            raise BizError(400, "关系起点与终点不能相同")
        src = await graph_repo.get_entity_by_id(session, sid)
        tgt = await graph_repo.get_entity_by_id(session, tid)
        if not src or not tgt or src.novel_id != r.novel_id or tgt.novel_id != r.novel_id:
            raise BizError(400, "起点或终点实体不存在")
        r.source_id, r.target_id = sid, tid
    await session.commit()
    return {"id": r.id, "source_id": r.source_id, "target_id": r.target_id, "type": r.type}


async def delete_relation(session: AsyncSession, owner_id: int, relation_id: int) -> dict:
    """物理删除关系。"""
    r = await graph_repo.get_relation_by_id(session, relation_id)
    if not r or r.owner_id != owner_id:
        raise BizError(404, "关系不存在")
    await graph_repo.delete_relation(session, r)
    await session.commit()
    return {"deleted": relation_id}
