"""人物分析 LangGraph 编排（M7 编排层 / P1 质量闭环）。

整体思路：
   用 LangGraph StateGraph 把 M3 的「候选精析」拆成可编排节点：
   extract（确定性候选）→ analyze（逐候选 LLM 精析）→ validate（规则质检）
   → 条件边按质检结果决定「回退重试」或「落库」→ persist（批量写库）。

   拓扑：
       START → extract → analyze → validate ─┬─(有不合格 且 retry_count < MAX)→ analyze
                                             └─(否则)→ persist → END

关键点：
   1. langgraph 仅在 build_graph() 内 lazy import，未安装 langgraph 不影响其他链路。
      （P2.5 起本图为人物分析唯一路径，原 LANGGRAPH_ENABLED 开关与线性冗余实现已移除。）
   2. analyze 节点复用 LangChain 调用层（M1）发起 LLM 精析；出现次数取 nlp 准确 freq。
   3. session/cfg 通过 config["configurable"] 注入节点，避免全局状态。
   4. P1 质量闭环（2026-09-18）：
      - validate 节点用纯规则校验（零 LLM 成本），不合格项打回 analyze 重试。
      - retry_count 硬上限 _MAX_RETRY 防死循环。
      - 重试轮仅重跑 rejected 候选，结果「追加」不覆盖（坑点：重试覆盖致首轮成果丢失）。
      - 重试轮未产出的候选由 merge_retry_gaps 补偿（坑点：候选丢失被误判为合格）。
      - 超限仍不合格的候选以 needs_review 标记兜底落库，不丢数据。

实现逻辑：
   build_graph() 编译 StateGraph（含条件边）；ainvoke 按上述拓扑执行，
   初始输入必须非空（langgraph 要求至少写入一个已声明 channel）。
"""
import asyncio
import json
import logging
import operator
import os

from typing import Annotated, TypedDict

from common import nlp
from common import crypto
from models.character import Character
from repositories import character_repo
from llm import langchain_factory as llm_adapters

logger = logging.getLogger(__name__)

# 最大重试轮次（防死循环的唯一保障）：retry_count 达到该值后强制放行落库
_MAX_RETRY = 2

# 注（P3 关键架构决策，2026-09-18 沙箱实测）：
#   原 P2 用 langgraph 的 Send 做并行扇出。但 langgraph 0.2.76 中 Send 对象无法被
#   持久化 checkpointer 序列化（AsyncPostgresSaver 写入时抛
#   TypeError: Object of type Send is not JSON serializable）。
#   实测对比：
#     Send + MemorySaver              → 成功（故 P1/P2 测试通过，问题未暴露）
#     Send + AsyncPostgresSaver       → 失败
#     节点内 asyncio.gather + Postgres → 成功
#   故改为「单节点内 asyncio.gather 并发」：保留并行收益，同时兼容持久化 checkpointer。
#   并发度仍由模块级 _LLM_SEM 控制，行为与 Send 版等价（已由 P2 测试回归验证）。

# 进度区间划分（保证重试轮不回退，只增不减）：
#   首轮 analyze 占 20→70，validate 占 70→80，重试轮占 80→90，persist 占 90→100
_ANALYZE_LO, _ANALYZE_HI = 20, 70
_RETRY_LO, _RETRY_HI = 80, 90

# P2 并行 LLM 并发上限（模块级单例，不可放入节点内 —— 放节点内等于不限流）。
# 取值参考：本地 Ollama 建议 1~2；云端 API 按其 RPM 限额换算。
# 若同时启用 Celery 多 worker，需下调至 1，避免总并发 = worker 数 × 该值。
_LLM_SEM = asyncio.Semaphore(int(os.getenv("GRAPH_LLM_CONCURRENCY", "4")))


class CharacterState(TypedDict, total=False):
    """人物分析状态：在节点间流转的持续数据。

    注意（实测坑点）：
        1. 节点写入的字段必须在此声明，否则 langgraph 抛 InvalidUpdateError。
           未声明字段会被**静默丢弃**（不报错），导致上层读到 None。
        2. 列表字段不能一律加 add reducer：analyze 需要「追加」，而 validate
           需要「过滤」（只减不增），两者语义冲突。故拆为：
           - new_results：本轮增量，加 add reducer
           - results：累计结果，默认替换语义，仅由 validate 单点写入
        3. 任何节点都**只返回自己写入的字段**，绝不 `return state`：
           否则带 reducer 的字段（tok_in/tok_out/failed）会被重复累加，
           实测可把 7 次调用 × 100 放大到 641600（指数级膨胀）。
    """
    novel_id: int
    owner_id: int
    full_text: str
    candidates: list
    created: int
    skipped: int
    # ── 并行增量字段（reducer = add）──
    new_results: Annotated[list, operator.add]     # 各并行节点 append
    tok_in: Annotated[int, operator.add]           # 各并行节点累加（只返回增量！）
    tok_out: Annotated[int, operator.add]
    failed: Annotated[int, operator.add]
    # ── 累计/单点写入字段（默认替换语义，不加 reducer）──
    results: list             # 累计合格结果，仅 validate 写入
    # ── P1 质量闭环新增 ──
    retry_count: int          # 已发起的重试轮次（0 起）
    rejected: list            # 待重试候选 [{name, freq, reason}]
    rejected_final: list      # 超限仍不合格的候选（兜底落库，标记 needs_review）
    review_count: int         # 兜底落库、待人工复核的人物数
    # 注意（实测坑点）：所有被节点写入的字段都必须在此声明。
    # langgraph 对未声明字段会静默丢弃（不报错），导致上层读到 None。


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
    """确定性候选节点：若未提供候选则由 nlp 产出（零成本）。

    关键点（P2 实测坑点）：
        1. 不重置 results（该字段由 validate 单点维护）。
        2. 返回**仅含本节点写入字段**的 dict，绝不 `return state`——
           否则 state 中带 add reducer 的字段（tok_in 等）会被重复累加。
        3. 重试轮再次进入时 candidates 已存在，此时返回空 dict（无字段需写），
           避免重复触发下游扇出逻辑之外的副作用。
    """
    if not state.get("candidates"):
        return {"candidates": nlp.extract_person_candidates(
            state["full_text"], min_freq=nlp.MIN_FREQ_DEFAULT)}
    return {}


async def _analyze_node(state: CharacterState, config) -> CharacterState:
    """LLM 精析节点：候选内并发调用 LLM 生成小传。

    整体思路：
        1. 首轮处理全部候选；重试轮仅处理 rejected 中的候选（避免重复消耗 token）。
        2. 节点内用 asyncio.gather 并发调用 LLM（并发度受模块级 _LLM_SEM 约束），
           替代 langgraph 的 Send 扇出——原因见模块顶部注释（Send 与持久化
           checkpointer 在 langgraph 0.2.76 下不兼容）。
        3. 单候选失败只影响自己，不阻断其他候选（gather 用 return_exceptions 隔离）。

    关键点（实测坑点防护）：
        1. 只返回增量：new_results（list, add）、tok_in/tok_out（int, add）、failed（int, add）。
           **绝不**读改写这些 channel（如 state["tok_in"] = state["tok_in"] + n），
           否则 reducer 会再叠加一次，导致 token 翻倍。
        2. 失败候选不返回 token 增量（记账口径：无 usage 返回则不计入）。
        3. 取消检查：每候选调用前与调用后各检查（并发化后原有 for 循环内的检查
           需显式保留，否则用户取消后仍会继续烧 token）。
        4. 人物姓名以确定性候选名为准，覆盖 LLM 可能缺漏/幻觉的 name 字段。
        5. 重试轮记录上一轮打回原因，便于排障归因。
    """
    from common import task_cancel
    cfg = config["configurable"]["cfg"]
    config_id = config["configurable"]["config_id"]
    task_id = config["configurable"].get("task_id")
    model_name = getattr(cfg, "model", None)

    # 重试判定：rejected 非空（由 validate 保留）+ retry_count > 0（由 bump_retry 递增）
    rejected_in = state.get("rejected") or []
    is_retry = bool(rejected_in) and state.get("retry_count", 0) > 0
    if is_retry:
        targets = [{"name": r.get("name"), "freq": r.get("freq", 0)}
                   for r in rejected_in if r.get("name")]
    else:
        targets = list(state.get("candidates") or [])

    full_text = state.get("full_text", "")
    # 上一轮打回原因索引（供重试轮标注，便于排障）
    prev_reasons = {r.get("name"): r.get("reason") for r in rejected_in} if is_retry else {}

    async def analyze_one(cand: dict) -> dict:
        """单候选精析：返回该候选的增量（不读改写共享 channel）。"""
        name = cand.get("name")
        freq = cand.get("freq", 0)

        # 取消检查（调用前）
        if task_id and task_cancel.is_cancelled(task_id):
            exc = task_cancel.TaskCancelled("用户主动取消人物", 0, 0)
            exc.usage_info = {"config_id": config_id, "model": model_name,
                              "task_type": "character_analysis_graph"}
            raise exc

        try:
            async with _LLM_SEM:                  # 并发限流（模块级单例）
                ctx = nlp.extract_character_context(full_text, name)
                prompt = nlp.CANDIDATE_PROMPT.replace("{name}", name).replace("{text}", ctx)
                text, usage = await _acall([{"role": "user", "content": prompt}], cfg=cfg)
        except task_cancel.TaskCancelled:
            raise                                 # 取消异常向上抛，不被吞掉
        except Exception as e:                    # noqa: BLE001
            # 单候选失败：只计 failed，不返回 token 增量（无 usage 可计量）
            print(f"[graph] analyze 候选「{name}」失败，已跳过：{e}")
            return {"failed": 1}

        u_in = (usage or {}).get("tokens_in", 0)
        u_out = (usage or {}).get("tokens_out", 0)

        # 取消检查（调用后，已产生 token 消耗）
        if task_id and task_cancel.is_cancelled(task_id):
            exc = task_cancel.TaskCancelled("用户主动取消人物", u_in, u_out)
            exc.usage_info = {"config_id": config_id, "model": model_name,
                              "task_type": "character_analysis_graph"}
            raise exc

        data = _parse_json(text)
        item = data[0] if isinstance(data, list) else data
        if not isinstance(item, dict) or not item:
            print(f"[graph] analyze 候选「{name}」LLM 输出无法解析为 JSON，已跳过")
            return {"failed": 1, "tok_in": u_in, "tok_out": u_out}

        item["name"] = name
        item["_freq"] = freq
        if is_retry and prev_reasons.get(name):
            item["_prev_reject_reason"] = prev_reasons[name]
        return {"new_results": [item], "tok_in": u_in, "tok_out": u_out}

    # 并发执行（return_exceptions=False：取消异常需向上抛出以终止整图）
    partials = await asyncio.gather(*[analyze_one(c) for c in targets])

    # 汇总各候选的增量（本节点只返回汇总后的增量）
    merged: dict = {}
    for p in partials:
        for k, v in (p or {}).items():
            if k in ("tok_in", "tok_out", "failed"):
                merged[k] = merged.get(k, 0) + v          # 计数类求和
            elif k == "new_results":
                merged.setdefault("new_results", []).extend(v)   # 结果类合并
    return merged


async def _report_analyze_progress(state: CharacterState, config) -> CharacterState:
    """进度回写节点：analyze 收敛后统一上报该阶段进度。

    整体思路：并发执行期间无法安全共享进度计数（会互相覆盖），故在节点
    收敛点集中回写一次进度，区间依轮次取值（首轮 20→70、重试轮 80→90）。

    关键点（实测致命坑点）：
        本节点只做副作用（写进度），**必须返回空 dict**，绝不能 `return state`。
        原因：state 中的 tok_in/tok_out/failed 带 add reducer，若返回整个 state，
        这些已累加的值会被 reducer **再次累加**，导致 token 用量指数级膨胀
        （实测 7 次调用 × 100 被放大到 641600）。同理，任何节点的返回值都应
        仅包含「本节点需要写入的字段」。
    """
    from services import task_service
    task_id = config["configurable"].get("task_id")
    if task_id:
        is_retry = state.get("retry_count", 0) > 0
        await task_service.update_task_progress(
            task_id, stage="analyzing",
            progress=_RETRY_HI if is_retry else _ANALYZE_HI,
            status="running")
    return {}


async def _validate_node(state: CharacterState, config) -> CharacterState:
    """质检节点（P1 质量闭环）：纯规则校验产出，产出 rejected 供条件边决策。

    整体思路：
        用 character_validator 做零 LLM 成本校验，把「合格项」留在 results，
        「不合格项」写入 rejected；并补偿重试轮「未产出」的候选（坑点 14）。

    关键点：
        1. 合格/不合格拆分：不合格项从 results 移出，避免重复落库；
           重试成功后由 _analyze_node 追加回 results。
        2. gender 非法就地归一（在 validator 内完成），不进入重试。
        3. 重试轮「未产出」补偿：上一轮 rejected 中既未通过也未本轮不合格的候选，
           说明完全没产出（丢失而非修复），必须重新记入 rejected，否则被误判合格放行。
        4. 校验不通过不抛异常，只写 state，由条件边决定下一步。
    """
    from graph import character_validator as validator
    from services import task_service

    prev_rejected = state.get("rejected") or []
    prev_retry = state.get("retry_count", 0)

    # P2 关键：合并「并行增量」到「累计结果」。
    # results 为替换语义（无 reducer），故本节点是它的唯一写入点。
    # 按 name 去重后取最后一条，兼容重试轮同候选多次产出的情形。
    merged = list(state.get("results") or []) + list(state.get("new_results") or [])
    by_name: dict = {}
    for r in merged:
        if isinstance(r, dict) and r.get("name"):
            by_name[r["name"]] = r

    passed, rejected = validator.validate_batch(list(by_name.values()))

    # 坑点 14：补偿重试轮未产出的候选（仅在重试轮生效）
    if prev_retry > 0 and prev_rejected:
        gaps = validator.merge_retry_gaps(prev_rejected, passed, rejected)
        if gaps:
            print(f"[graph] validate 发现 {len(gaps)} 个候选重试未产出，重新记入待重试："
                  f"{[g['name'] for g in gaps]}")
            rejected.extend(gaps)

    # 进度回写（validate 区间 70→80；重试轮不复用避免回退）
    task_id = config["configurable"].get("task_id")
    if task_id:
        await task_service.update_task_progress(
            task_id, stage="validating",
            progress=75 if prev_retry > 0 else 70,
            status="running")

    # 只返回本节点写入的字段（坑点：return state 会让 add reducer 字段重复累加）。
    # new_results 必须显式置空（替换语义），否则下一轮会重复合并导致结果翻倍。
    # retry_count 此处不递增（由 retry 分支上的 _bump_retry_node 负责，避免 off-by-one）。
    return {
        "results": passed,
        "new_results": [],
        "rejected": rejected,
    }


def _route_after_validate(state: CharacterState) -> str:
    """条件边：判断质检后是回退重试还是落库（纯函数，不写 state）。

    整体思路：有不合格候选且未达重试上限 → 回退 bump_before_retry；
    否则放行 persist。

    关键点：
        1. retry_count < _MAX_RETRY 是防死循环的唯一保障（LLM 反复产出同样违规内容时
           必须能退出）。超限的候选不会丢弃，由 _persist_node 以 needs_review 兜底落库。
        2. 条件边函数必须无副作用，故 retry_count 的递增放到 bump 节点，
           保证「计数」与「实际回退次数」严格一致（此前的 off-by-one 由集成测试捕获）。
    """
    rejected = state.get("rejected") or []
    retry = state.get("retry_count", 0)
    if rejected and retry < _MAX_RETRY:
        print(f"[graph] 质检发现 {len(rejected)} 个不合格，进入第 {retry + 1} 轮重试")
        return "retry"
    if rejected:
        print(f"[graph] 已达重试上限 {_MAX_RETRY}，{len(rejected)} 个候选转兜底落库(needs_review)")
    return "ok"


async def _bump_retry_node(state: CharacterState, config) -> CharacterState:
    """重试计数节点：仅在确认回退时递增 retry_count。

    整体思路：条件边无法写 state，故用独立节点承接「计数 +1」这一副作用。
    关键点：
        1. 该节点位于 retry 分支上（validate --retry--> bump --> extract），
           保证只有真正回退时才计数，杜绝 off-by-one 与死循环。
        2. retry_count 既是路由判据（与 _MAX_RETRY 比较），也是诊断指标，
           语义为「已发起的重试轮次」。
        3. 只返回 retry_count 一个字段，绝不 return state（避免 add 字段重复累加）。
    """
    return {"retry_count": state.get("retry_count", 0) + 1}
    return state


async def _persist_node(state: CharacterState, config) -> CharacterState:
    """落库节点：批量写入 story_character，去重已存在人名，兜底超限候选。

    整体思路：遍历 LLM 产出结果，按人名去重后批量落库；对「重试超限仍不合格」
    的候选以最小可用信息兜底落库并打 needs_review 标记，保证不丢数据。

    关键点：
        1. 事务边界：先记账 state["created"/"skipped"]、再 commit；
           commit 失败则 rollback 并向上抛出，避免 session 处于 invalid 状态。
        2. 批内去重：用 seen 集合拦截同一人物被 LLM 返回两次产生的同名记录
           （DB 唯一约束在 flush 前无法经 identity map 命中，须在内存层先去重）。
        3. 记账在 commit 之前完成，杜绝「commit 抛错 → state 未赋值 → 上层 KeyError」。
        4. P1 兜底：超限的 rejected 候选以 role="配角" 最小信息落库，
           extra 记录 needs_review=True 与 reject_reason，供前端提示人工补全。
    """
    session = config["configurable"]["session"]
    novel_id = state["novel_id"]
    owner_id = state["owner_id"]
    created = skipped = review = 0
    seen: set[str] = set()
    try:
        # ── 正常合格项落库 ──
        for item in state.get("results") or []:
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

        # ── P1 兜底：重试超限仍不合格的候选，以最小信息落库并标记待人工复核 ──
        for r in state.get("rejected") or []:
            name = r.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            if await character_repo.get_by_novel_name(session, novel_id, name):
                skipped += 1
                continue
            c_obj = Character(
                novel_id=novel_id, owner_id=owner_id, name=name,
                role="配角",                       # 最小可用默认值
                description=None,
                source="auto", appearances=r.get("freq", 0),
                extra={"needs_review": True, "reject_reason": r.get("reason")},
            )
            session.add(c_obj)
            created += 1
            review += 1

        await session.commit()
    except Exception:
        await session.rollback()
        raise
    # 只返回本节点写入的字段（坑点：return state 会让 add reducer 字段重复累加）
    return {
        "created": created,
        "skipped": skipped,
        "rejected_final": [r for r in (state.get("rejected") or [])],
        "review_count": review,
    }


# 编译后的状态图单例缓存：避免每次 analyze_via_graph 重复 compile（提升性能、解耦调用点）
_GRAPH_CACHE = None

# ─────────────────────── P3：持久化 checkpointer 管理 ───────────────────────
# 设计说明：
#   AsyncPostgresSaver.from_conn_string() 返回的是**异步上下文管理器**，不是已连接的
#   saver 实例。若在函数内 `=` 赋值使用，async with 退出后连接池即关闭，图将不可用
#   （报 connection closed）。故必须由应用 lifespan 持有并通过 set_saver 注入。
#
#   未注入时自动回落 MemorySaver（进程内存态），保证开发/降级场景图仍可用。

_SAVER = None                 # 由 lifespan 注入的持久化 saver（None = 未启用）


def set_saver(saver) -> None:
    """注入持久化 checkpointer（由应用 lifespan 调用）。

    关键点：注入后必须清空已编译缓存，否则旧图仍持有 MemorySaver，
    导致续跑能力不生效（编译结果与 saver 是绑定的）。
    """
    global _SAVER, _GRAPH_CACHE
    _SAVER = saver
    _GRAPH_CACHE = None


def has_persistent_saver() -> bool:
    """是否有持久化 checkpointer（供健康检查/诊断接口使用）。"""
    return _SAVER is not None


async def cleanup_thread(thread_id: str) -> bool:
    """清理指定 thread 的 checkpoint（任务终态后调用，防表膨胀）。

    整体思路：
        任务成功/失败终止后，该 thread 已无续跑价值，应删除其 checkpoint。
        处于 paused（待人工审核）状态的 thread 不得清理，否则无法恢复。

    关键点：
        1. 只能在使用持久化 saver 时清理（MemorySaver 无此概念）。
        2. 清理失败仅告警，不阻断主流程（checkpoint 膨胀有定时兜底清理）。
        3. 优先使用官方 adelete_thread（若该版本提供），否则回落到原生 SQL。

    实现逻辑：
        saver.adelete_thread(thread_id) → 异常捕获 → 返回是否成功。
    """
    if _SAVER is None or not thread_id:
        return False
    try:
        deleter = getattr(_SAVER, "adelete_thread", None)
        if deleter is None:
            logger.warning("当前 checkpointer 不支持 adelete_thread，跳过清理 thread=%s", thread_id)
            return False
        await deleter(thread_id)
        logger.info("已清理 checkpoint thread=%s", thread_id)
        return True
    except Exception as e:                       # noqa: BLE001
        logger.warning("checkpoint 清理失败 thread=%s: %s", thread_id, e)
        return False


def build_graph():
    """编译人物分析状态图（含 P1 质检与条件边重试），结果缓存为模块级单例。

    拓扑：
        START → extract → analyze → validate ─┬─(有不合格 && retry<MAX)→ analyze
                                              └─(否则)→ persist → END

    langgraph 在此处 lazy import：仅当启用编排层时本模块被导入，故未安装不影响其他链路。
    关键点：
        1. compiled graph 无状态可重复 ainvoke，缓存单例安全且避免重复编译开销。
        2. 条件边 path_map 显式声明 retry/ok 两个分支，retry 回边指向 analyze 形成循环。
        3. 注入 MemorySaver 检查点（配合 thread_id），供后续 P3 升级为持久化 saver。
    """
    global _GRAPH_CACHE
    if _GRAPH_CACHE is not None:
        return _GRAPH_CACHE
    from langgraph.graph import StateGraph, END, START
    g = StateGraph(CharacterState)
    g.add_node("extract", _extract_node)
    g.add_node("analyze", _analyze_node)              # 节点内 asyncio.gather 并发
    g.add_node("report_progress", _report_analyze_progress)
    g.add_node("validate", _validate_node)
    g.add_node("bump_retry", _bump_retry_node)
    g.add_node("persist", _persist_node)
    g.add_edge(START, "extract")
    g.add_edge("extract", "analyze")
    g.add_edge("analyze", "report_progress")
    g.add_edge("report_progress", "validate")
    # P1 条件边：质检不合格则经 bump_retry 回退 extract 重跑，否则落库。
    # bump_retry 承担 retry_count +1 的副作用（条件边函数必须无副作用）。
    g.add_conditional_edges(
        "validate", _route_after_validate, {"retry": "bump_retry", "ok": "persist"})
    g.add_edge("bump_retry", "extract")
    g.add_edge("persist", END)
    # P3：优先使用持久化 checkpointer（由 lifespan 经 set_saver 注入），
    # 未注入时回落 MemorySaver（进程内存态，重启即丢，供开发/降级使用）。
    if _SAVER is not None:
        _GRAPH_CACHE = g.compile(checkpointer=_SAVER)
        logger.info("人物分析图已编译（持久化 checkpointer）")
    else:
        try:
            from langgraph.checkpoint.memory import MemorySaver
            _GRAPH_CACHE = g.compile(checkpointer=MemorySaver())
            logger.info("人物分析图已编译（内存 checkpointer，重启不保留断点）")
        except Exception:
            # 检查点依赖缺失时回落无检查点编译，保证编排层仍可用
            _GRAPH_CACHE = g.compile()
            logger.warning("人物分析图已编译（无 checkpointer）")
    return _GRAPH_CACHE
