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
    failed: int
    tok_in: int
    tok_out: int


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


async def _acall(messages, *, cfg):
    """经 LangChain 调用层发起人物精析 LLM 调用（M7 复用 M1）。
    返回 (文本, usage_dict)。usage 由调用方累积，任务结束时统一写入 DB。
    """
    from llm.langchain_factory import get_langchain_model, invoke_with_usage
    model = get_langchain_model(cfg)
    resp, usage = await invoke_with_usage(model, messages)
    content = getattr(resp, "content", None)
    return (content if isinstance(content, str) else str(resp), usage)


# 注：_CANDIDATE_PROMPT 与人物上下文抽取已统一收敛到 common.nlp（见 nlp.CANDIDATE_PROMPT /
# nlp.extract_character_context），本模块直接复用，避免双份实现漂移（P1-9）。


# ───────────────────────────── LangGraph 节点 ─────────────────────────────

async def _extract_node(state: CharacterState, config) -> CharacterState:
    """确定性候选节点：若未提供候选则由 nlp 产出（零成本）。"""
    if not state.get("candidates"):
        state["candidates"] = nlp.extract_person_candidates(state["full_text"], min_freq=nlp.MIN_FREQ_DEFAULT)
    state["results"] = []
    return state


async def _analyze_node(state: CharacterState, config) -> CharacterState:
    """LLM 精析节点：逐候选生成小传，出现次数取 nlp 准确 freq。

    整体思路：每个候选独立调用 LLM，单候选失败（网络/解析/超时）不影响整体，
    跳过并计数，已成功的结果仍保留，避免「一个候选崩→整个任务失败且已落库数据丢失」。

    关键点（P0-1 / P1-5 修复）：
        1. 逐候选 try/except：单候选异常仅记录，继续下一个，task 状态最终 success（部分跳过）。
        2. token 按调用累加，跳过候选不计入。
        3. 失败计数写回 state["failed"]，供上层任务进度/日志观测。
        4. P0-1：人物姓名以确定性候选 c["name"] 为准（覆盖 LLM 可能缺漏/幻觉的 name 字段），
           不再依赖 LLM 回传 name，杜绝「LLM 严格输出合规 JSON 却被静默丢弃」的故障。
        5. P1-5：逐候选检查任务取消（task_cancel），取消即携累积 token 抛出，
           供 analyze_via_graph 透传至外层统一写 usage；并逐候选回写进度。
    """
    from services import task_service
    from common import task_cancel
    cfg = config["configurable"]["cfg"]
    config_id = config["configurable"]["config_id"]
    task_id = config["configurable"].get("task_id")
    model_name = getattr(cfg, "model", None)
    owner_id = state["owner_id"]
    candidates = state["candidates"]
    total = max(1, len(candidates))
    results = []
    failed = 0
    for idx, c in enumerate(candidates):
        name = c["name"]
        freq = c.get("freq", 0)
        # P1-5：用户取消检查（携已累积 token 抛出，外层统一写 usage）
        if task_id and task_cancel.is_cancelled(task_id):
            exc = task_cancel.TaskCancelled(
                "用户主动取消人物", state.get("tok_in", 0), state.get("tok_out", 0))
            exc.usage_info = {"config_id": config_id, "model": model_name,
                              "task_type": "character_analysis_graph"}
            raise exc
        try:
            ctx = nlp.extract_character_context(state["full_text"], name)
            prompt = nlp.CANDIDATE_PROMPT.replace("{name}", name).replace("{text}", ctx)
            text, usage = await _acall([{"role": "user", "content": prompt}], cfg=cfg)
            if usage:
                state["tok_in"] = state.get("tok_in", 0) + usage.get("tokens_in", 0)
                state["tok_out"] = state.get("tok_out", 0) + usage.get("tokens_out", 0)
            data = _parse_json(text)
            item = data[0] if isinstance(data, list) else data
            # P0-1：以确定性候选名为准，覆盖 LLM 可能缺漏/幻觉的 name 字段
            if isinstance(item, dict) and item:
                item["name"] = name
                item["_freq"] = freq
                results.append(item)
        except Exception as e:
            failed += 1
            # 单候选失败跳过，不阻断整体；记录避免静默丢失
            print(f"[graph] analyze 候选「{name}」失败，已跳过：{e}")
        # P1-5：逐候选回写进度（analyzing 区间 20→85）
        if task_id:
            prog = 20 + int((idx + 1) / total * 65)
            await task_service.update_task_progress(
                task_id, stage="analyzing", progress=prog, status="running")
    state["results"] = results
    state["failed"] = state.get("failed", 0) + failed
    return state


async def _persist_node(state: CharacterState, config) -> CharacterState:
    """落库节点：批量写入 story_character，去重已存在人名。

    整体思路：遍历 LLM 产出结果，按人名去重后批量落库。

    关键点（P1-8 修复）：
        1. 事务边界：先记账 state["created"/"skipped"]、再 commit；
           commit 失败则 rollback 并向上抛出，避免 session 处于 invalid 状态。
        2. 批内去重：用 seen 集合拦截同一人物被 LLM 返回两次产生的同名记录
           （DB 唯一约束在 flush 前无法经 identity map 命中，须在内存层先去重）。
        3. 记账在 commit 之前完成，杜绝「commit 抛错 → state 未赋值 → 上层 KeyError」。
    """
    session = config["configurable"]["session"]
    novel_id = state["novel_id"]
    owner_id = state["owner_id"]
    created = skipped = 0
    seen: set[str] = set()
    try:
        for item in state["results"]:
            name = item.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
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
        # 先记账，再提交：commit 失败则 rollback，state 仍保留已尝试计数
        state["created"] = created
        state["skipped"] = skipped
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return state


# 编译后的状态图单例缓存：避免每次 analyze_via_graph 重复 compile（提升性能、解耦调用点）
_GRAPH_CACHE = None


def build_graph():
    """编译人物分析状态图（extract→analyze→persist），结果缓存为模块级单例。

    langgraph 在此处 lazy import：仅当启用编排层时本模块被导入，故未安装不影响其他链路。
    关键点：compiled graph 无状态可重复 ainvoke，缓存单例安全且避免重复编译开销。
    """
    global _GRAPH_CACHE
    if _GRAPH_CACHE is not None:
        return _GRAPH_CACHE
    from langgraph.graph import StateGraph, END, START
    g = StateGraph(CharacterState)
    g.add_node("extract", _extract_node)
    g.add_node("analyze", _analyze_node)
    g.add_node("persist", _persist_node)
    g.add_edge(START, "extract")
    g.add_edge("extract", "analyze")
    g.add_edge("analyze", "persist")
    g.add_edge("persist", END)
    # P1-6：注入 MemorySaver 检查点，使状态图具备「可中断/可恢复」能力
    # （配合 analyze_via_graph 传入的 thread_id 使用，支持 Command(resume=...) 重放）。
    try:
        from langgraph.checkpoint.memory import MemorySaver
        _GRAPH_CACHE = g.compile(checkpointer=MemorySaver())
    except Exception:
        # 检查点依赖缺失时回落无检查点编译，保证编排层仍可用
        _GRAPH_CACHE = g.compile()
    return _GRAPH_CACHE
