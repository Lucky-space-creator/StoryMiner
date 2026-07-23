"""人物分析 LangGraph 编排（M7 编排层）。

整体思路：
    用 LangGraph StateGraph 把 M3 的「候选精析」拆成可编排节点：
    extract（确定性候选）→ analyze（逐候选 LLM 精析）→ persist（批量写库）。
    状态在节点间流转，便于后续扩展（去重/质检/回退节点）。

关键点：
    1. langgraph 仅在 build_graph() 内 lazy import（仅当 LANGGRAPH_ENABLED 启用、本模块被导入时才触发），
       未安装 langgraph 不影响其他链路。
    2. analyze 节点复用 LangChain 调用层（M1）发起 LLM 精析；出现次数取 nlp 准确 freq。
    3. session/cfg 通过 config["configurable"] 注入节点，避免全局状态。

实现逻辑：
    build_graph() 编译 StateGraph；ainvoke 按 extract→analyze→persist 顺序执行。
"""
import json
import re

from typing import TypedDict

from common import nlp
from common import crypto
from models.character import Character
from repositories import character_repo
from llm import langchain_factory as llm_adapters


class CharacterState(TypedDict, total=False):
    """人物分析状态：在节点间流转的持续数据。"""
    novel_id: int
    owner_id: int
    full_text: str
    candidates: list
    results: list
    created: int
    skipped: int


def _parse_json(raw: str) -> dict | list:
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


async def _acall(messages, *, owner_id: int, task_type: str, config_id: int, cfg):
    """经 LangChain 调用层发起人物精析 LLM 调用（M7 复用 M1）。

    返回 (文本, usage)；usage 已由 invoke_with_usage 写入 story_llm_usage，故返回 None。
    """
    from llm.langchain_factory import get_langchain_model, invoke_with_usage
    model = get_langchain_model(cfg)
    resp = await invoke_with_usage(
        model, messages, owner_id=owner_id, task_type=task_type, config_id=config_id)
    content = getattr(resp, "content", None)
    return (content if isinstance(content, str) else str(resp), None)


_CANDIDATE_PROMPT = """你是小说人物小传撰写助手。请为【指定人物】生成结构化档案。
仅输出一个 JSON 对象，不要包含任何解释或 markdown 标记：
{"role":"主角/配角/反派","gender":"男/女/未知","identity":"身份或职业","personality":"性格特点","appearance":"外貌特征","catchphrase":"口头禅","description":"150字以内的人物小传，包含大致经历"}
人物姓名：{name}
相关小说片段：
{text}"""


def _extract_context(text: str, name: str, max_chars: int = 4000) -> str:
    """取含指定人名的相关片段拼接（候选精析喂料），截断到 max_chars。"""
    idxs = [m.start() for m in re.finditer(re.escape(name), text)]
    if not idxs:
        return text[:max_chars]
    snippets, total = [], 0
    for i in idxs:
        s, e = max(0, i - 200), min(len(text), i + 400)
        snippets.append(text[s:e])
        total += (e - s)
        if total >= max_chars:
            break
    return "\n……\n".join(snippets)[:max_chars]


# ───────────────────────────── LangGraph 节点 ─────────────────────────────

async def _extract_node(state: CharacterState, config) -> CharacterState:
    """确定性候选节点：若未提供候选则由 nlp 产出（零成本）。"""
    if not state.get("candidates"):
        state["candidates"] = nlp.extract_person_candidates(state["full_text"], min_freq=2)
    state["results"] = []
    return state


async def _analyze_node(state: CharacterState, config) -> CharacterState:
    """LLM 精析节点：逐候选生成小传，出现次数取 nlp 准确 freq。"""
    cfg = config["configurable"]["cfg"]
    config_id = config["configurable"]["config_id"]
    owner_id = state["owner_id"]
    results = []
    for c in state["candidates"]:
        name = c["name"]
        freq = c.get("freq", 0)
        ctx = _extract_context(state["full_text"], name)
        prompt = _CANDIDATE_PROMPT.replace("{name}", name).replace("{text}", ctx)
        text, _usage = await _acall(
            [{"role": "user", "content": prompt}], owner_id=owner_id,
            task_type="character_analysis", config_id=config_id, cfg=cfg)
        data = _parse_json(text)
        item = data[0] if isinstance(data, list) else data
        if isinstance(item, dict) and item.get("name"):
            item["_freq"] = freq
            results.append(item)
    state["results"] = results
    return state


async def _persist_node(state: CharacterState, config) -> CharacterState:
    """落库节点：批量写入 story_character，去重已存在人名。"""
    session = config["configurable"]["session"]
    novel_id = state["novel_id"]
    owner_id = state["owner_id"]
    created = skipped = 0
    for item in state["results"]:
        name = item.get("name")
        if not name:
            continue
        if await character_repo.get_by_novel_name(session, novel_id, name):
            skipped += 1
            continue
        c_obj = Character(
            novel_id=novel_id, owner_id=owner_id, name=name,
            role=(item.get("role") or "配角").strip() or "配角",
            gender=(item.get("gender") or "").strip() or None,
            identity=(item.get("identity") or "").strip() or None,
            personality=(item.get("personality") or "").strip() or None,
            appearance=(item.get("appearance") or "").strip() or None,
            catchphrase=(item.get("catchphrase") or "").strip() or None,
            description=(item.get("description") or "").strip()[:150] or None,
            source="auto", appearances=item.get("_freq", 0),
        )
        session.add(c_obj)
        created += 1
    await session.commit()
    state["created"] = created
    state["skipped"] = skipped
    return state


def build_graph():
    """编译人物分析状态图（extract→analyze→persist）。

    langgraph 在此处 lazy import：仅当启用编排层时本模块被导入，故未安装不影响其他链路。
    """
    from langgraph.graph import StateGraph, END, START
    g = StateGraph(CharacterState)
    g.add_node("extract", _extract_node)
    g.add_node("analyze", _analyze_node)
    g.add_node("persist", _persist_node)
    g.add_edge(START, "extract")
    g.add_edge("extract", "analyze")
    g.add_edge("analyze", "persist")
    g.add_edge("persist", END)
    return g.compile()
