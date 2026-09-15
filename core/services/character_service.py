"""
人物档案业务逻辑（M6 小说人物信息简介）

整体思路：
    封装两类能力：(1) 人物卡 CRUD（列表/新建/详情/编辑/删除）；(2) AI 生成小传，
    调用本地 Ollama 大模型基于全文生成结构化人物档案并写回。与 M5 图谱实体解耦。

关键点：
    1. 同小说人物名唯一，新建前查重，冲突抛 BizError。
    2. AI 生成复用 M9 对话模型分发链（list_for_dispatch + 适配器），失败不破坏原数据。
    3. 生成时统计人物名在章节文本中的出现次数，回填 appearances 供前端展示。

实现逻辑：
    CRUD 校验归属后委托 character_repo；generate 拼文本→调 chat→容错解析 JSON→更新字段。
"""
import json
import logging
import time
import traceback
import asyncio

from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from models.character import Character
from repositories import character_repo, novel_repo, llm_repo
from services import task_service
from llm import langchain_factory as llm_adapters
from common import crypto
from common import task_cancel
from common.exceptions import BizError
from common import nlp
from config import LANGGRAPH_ENABLED

logger = logging.getLogger(__name__)

# 人物分析 LangGraph 整体超时上限（秒）：避免单个候选 LLM 长尾导致整图无限等待。
# 正常几候选 ~ 数十秒~数分钟；30 分钟上限覆盖长文多候选，超时即失败可重试。
CHARACTER_GRAPH_TIMEOUT = int(__import__("os").getenv("CHARACTER_GRAPH_TIMEOUT", "1800"))

# 前端展示字段映射：列表项仅需 name/role/desc/appearances
_LIST_FIELDS = ("id", "name", "role", "description", "appearances")


def _to_list_item(c: Character) -> dict:
    """列表项：映射 description→desc（前端字段名）。"""
    return {
        "id": c.id, "name": c.name, "role": c.role,
        "desc": c.description, "appearances": c.appearances,
    }


def _to_detail(c: Character) -> dict:
    """详情：返回完整档案，含关键事件（key_events，从 extra JSONB 提取）。"""
    extra = c.extra or {}
    return {
        "id": c.id, "name": c.name, "role": c.role, "gender": c.gender,
        "identity": c.identity, "personality": c.personality,
        "appearance": c.appearance, "catchphrase": c.catchphrase,
        "desc": c.description, "avatar": c.avatar, "source": c.source,
        "appearances": c.appearances,
        "key_events": extra.get("key_events", []),
    }


async def _get_chat_adapter(session: AsyncSession, owner_id: int):
    """取默认对话模型适配器（M9 分发链），返回 (adapter, model_name)。"""
    cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
    if not cfgs:
        raise BizError(400, "尚未配置对话模型（llm_type=chat），请先在模型管理中添加")
    cfg = cfgs[0]
    return llm_adapters.get_adapter(cfg, crypto.decrypt(cfg.api_key)), cfg.model


async def list_characters(session: AsyncSession, novel_id: int) -> list[dict]:
    """人物列表（M6.1）。"""
    chars = await character_repo.list_by_novel(session, novel_id)
    return [_to_list_item(c) for c in chars]


async def create_character(session: AsyncSession, novel_id: int, owner_id: int, payload: dict) -> dict:
    """新建人物（M6.1/M6.7），校验同小说名唯一。"""
    name = (payload.get("name") or "").strip()
    if not name:
        raise BizError(400, "人物姓名不能为空")
    if await character_repo.get_by_novel_name(session, novel_id, name):
        raise BizError(409, f"该小说已存在人物「{name}」")
    c = Character(
        novel_id=novel_id, owner_id=owner_id, name=name,
        role=(payload.get("role") or "配角").strip() or "配角",
        description=(payload.get("desc") or "").strip() or None,
        source="manual",
    )
    await character_repo.create(session, c)
    await session.commit()
    return _to_detail(c)


async def get_character(session: AsyncSession, char_id: int, owner_id: int) -> dict:
    """人物详情（M6.1）。"""
    c = await character_repo.get(session, char_id)
    if not c or c.owner_id != owner_id or c.deleted_at is not None:
        raise BizError(404, "人物不存在")
    return _to_detail(c)


async def update_character(session: AsyncSession, char_id: int, owner_id: int, payload: dict) -> dict:
    """编辑人物档案（M6.7），仅更新传入字段。"""
    c = await character_repo.get(session, char_id)
    if not c or c.owner_id != owner_id or c.deleted_at is not None:
        raise BizError(404, "人物不存在")
    for fld in ("role", "gender", "identity", "personality", "appearance", "catchphrase", "description", "avatar"):
        key = "desc" if fld == "description" else fld
        if key in payload and payload[key] is not None:
            setattr(c, fld, payload[key])
    await session.commit()
    return _to_detail(c)


async def delete_character(session: AsyncSession, char_id: int, owner_id: int) -> None:
    """删除人物（物理删除）。"""
    c = await character_repo.get(session, char_id)
    if not c or c.owner_id != owner_id:
        raise BizError(404, "人物不存在")
    await character_repo.hard_delete(session, char_id)
    await session.commit()


# AI 生成小传提示词：约束结构化 JSON 输出
_GEN_PROMPT = """你是小说人物小传撰写助手，请基于给定文本为该人物生成结构化档案。
仅输出一个 JSON 对象，不要包含任何解释或 markdown 标记，格式严格如下：
{
  "role":"主角",
  "gender":"男",
  "identity":"身份/职业",
  "personality":"性格特点",
  "appearance":"外貌描写",
  "catchphrase":"口头禅",
  "description":"150字左右的人物小传"
}
人物姓名：{name}
相关文本：
{text}"""


async def generate_profile(session: AsyncSession, char_id: int, owner_id: int, task_id: int | None = None) -> dict:
    """AI 生成小传（M6.2）：基于全文分片流式生成结构化档案并写回。

    改进点：
        1. 正文超长时按分片处理，突破单 prompt 1.2 万字截断，覆盖完整全文。
        2. 流式调用逐片回写进度（30→90）。
        3. 多分片小传按字段合并（description 取最长）。
    """
    c = await character_repo.get(session, char_id)
    if not c or c.owner_id != owner_id or c.deleted_at is not None:
        raise BizError(404, "人物不存在")
    chapters = await novel_repo.list_all_chapters(session, c.novel_id)
    if not chapters:
        raise BizError(400, "该小说暂无章节，无法生成小传")
    full_text = "\n".join(ch.content for ch in chapters)
    chunks = _chunk_text(full_text) or [""]
    adapter, model_name = await _get_chat_adapter(session, owner_id)
    cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
    config_id = cfgs[0].id if cfgs else None
    profiles: list[dict] = []
    tok_in = tok_out = 0
    n = len(chunks)
    for i, chunk in enumerate(chunks):
        prompt = _GEN_PROMPT.replace("{name}", c.name).replace("{text}", chunk)
        lo = 30 + int(i / n * 60)   # 进度区间：30 → 90
        hi = 30 + int((i + 1) / n * 60)
        raw = await _stream_collect(
            adapter, [{"role": "user", "content": prompt}], task_id,
            lo=lo, hi=hi, stage="generating", temperature=0.5, max_tokens=1500,
        )
        u = adapter.get_last_usage()
        if u:
            tok_in += u.get("tokens_in", 0)
            tok_out += u.get("tokens_out", 0)
        # 用户主动取消：回写已消耗 token 至任务记录，raise 由外层统一写入 story_llm_usage
        if task_id and task_cancel.is_cancelled(task_id):
            await session.rollback()
            if task_id:
                await task_service.update_task_progress(task_id, tokens_in=tok_in, tokens_out=tok_out)
            exc = task_cancel.TaskCancelled("用户主动取消小传生成", tok_in, tok_out)
            exc.usage_info = {"config_id": config_id, "model": model_name}
            raise exc
        d = _parse_json(raw)
        if isinstance(d, dict):
            profiles.append(d)
    data = _merge_bio(profiles)
    if data:
        c.role = data.get("role") or c.role
        c.gender = data.get("gender") or c.gender
        c.identity = data.get("identity") or c.identity
        c.personality = data.get("personality") or c.personality
        c.appearance = data.get("appearance") or c.appearance
        c.catchphrase = data.get("catchphrase") or c.catchphrase
        c.description = data.get("description") or c.description
        c.source = "auto"
        # 出场次数基于全量正文统计（不再受截断影响）
        c.appearances = full_text.count(c.name)
        await session.commit()
    # token 由 generate_profile_async 终态统一写入 story_llm_usage
    if task_id:
        await task_service.update_task_progress(task_id, tokens_in=tok_in, tokens_out=tok_out)
    # 返回 (人物详情, 用量元信息) 供外层统一写 DB
    token_meta = {"tokens_in": tok_in, "tokens_out": tok_out, "config_id": config_id, "model": model_name}
    return _to_detail(c), token_meta


def _parse_json(raw: str) -> dict:
    """容错解析 LLM 输出的 JSON（去 markdown 包裹、截取首尾花括号）。"""
    # 兼容部分模型把内容以列表（内容块）形式返回的情况，先拼成文本
    if isinstance(raw, list):
        raw = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in raw)
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    # 兼容顶层为 JSON 数组或对象：人物分析返回 [...]，图谱返回 {...}。
    # 取更外层的包裹，避免把数组误截成「{...},{...}」导致 json.loads 失败。
    obj_start, obj_end = raw.find("{"), raw.rfind("}")
    arr_start, arr_end = raw.find("["), raw.rfind("]")
    if arr_start != -1 and (obj_start == -1 or arr_start < obj_start):
        raw = raw[arr_start:arr_end + 1]
    elif obj_start != -1 and obj_end != -1:
        raw = raw[obj_start:obj_end + 1]
    try:
        return json.loads(raw)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 长文分片 / 跨片合并 / 流式收集 工具函数
# ---------------------------------------------------------------------------
_CHUNK_MAX_CHARS = 6000


def _chunk_text(text: str, max_chars: int = _CHUNK_MAX_CHARS) -> list[str]:
    """将长文按字符窗口切分为多个片段，优先在换行/句末断句，避免切碎句子。

    关键点：
        1. 单段不超过 max_chars，用于突破单次 prompt 的 1 万字硬截断上限。
        2. 断点优先选换行/句号等标点，减少语义割裂。
        3. 返回片段列表，调用方按阅读顺序逐片分析（主要人物多在前段）。
    """
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end >= n:
            chunks.append(text[start:])
            break
        cut = -1
        for sep in ("\n", "。", "！", "？", "!", "?"):
            idx = text.rfind(sep, start, end)
            if idx > cut:
                cut = idx
        if cut < start:
            cut = end - 1
        chunks.append(text[start:cut + 1])
        start = cut + 1
    return chunks


def _merge_characters(items: list[dict]) -> list[dict]:
    """跨分片抽取结果按姓名去重合并：保留首个，字段缺失时补后续非空值，description 取最长。"""
    by_name: dict[str, dict] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key not in by_name:
            by_name[key] = dict(it)
            continue
        cur = by_name[key]
        for f in ("role", "gender", "identity", "personality", "appearance", "catchphrase", "description"):
            v = (it.get(f) or "").strip()
            if not v:
                continue
            cur_v = (cur.get(f) or "").strip()
            if not cur_v:
                cur[f] = v
            elif f == "description" and len(v) > len(cur_v):
                cur[f] = v
    return list(by_name.values())


def _merge_bio(profiles: list[dict]) -> dict:
    """单人物多分片小传合并：各字段取首个非空值，description 取最长。"""
    merged: dict = {}
    for p in profiles:
        if not isinstance(p, dict):
            continue
        for f in ("role", "gender", "identity", "personality", "appearance", "catchphrase", "description"):
            v = (p.get(f) or "").strip()
            if not v:
                continue
            cur = (merged.get(f) or "").strip()
            if not cur:
                merged[f] = v
            elif f == "description" and len(v) > len(cur):
                merged[f] = v
    return merged


async def _stream_collect(adapter, messages: list[dict], task_id, lo: int, hi: int,
                          stage: str = "analyzing", **opts) -> str:
    """流式调用 chat_stream，边收边回写进度，返回完整文本；usage 由 adapter 记录。

    关键点：
        1. 复用适配器 chat_stream 异步生成器，逐 piece 累积完整文本。
        2. 每 ~0.4s 按已收字符量在 [lo, hi] 区间回写进度，避免长文等待期进度卡死。
        3. task_id 为 None 时不回写（如同步路径）。
    """
    collected: list[str] = []
    total = 0
    last = time.monotonic()
    async for piece in adapter.chat_stream(messages, **opts):
        collected.append(piece)
        total += len(piece)
        now = time.monotonic()
        if task_id is not None and now - last >= 0.4:
            frac = min(1.0, total / 2500.0)
            prog = lo + int(frac * (hi - lo))
            await task_service.update_task_progress(task_id, stage=stage, progress=prog, status="running")
            last = now
    return "".join(collected)


async def generate_profile_async(char_id: int, owner_id: int, task_id: int | None = None) -> None:
    """后台生成小传：自开会话调用 generate_profile，进度回写统一任务，异常仅记录不抛出。
    token 用量由终态 update_task_progress 统一写入 story_llm_usage，不逐次写 DB。
    """
    from db import SessionLocal
    async with SessionLocal() as session:
        token_meta = None
        try:
            if task_id:
                await task_service.update_task_progress(task_id, stage="generating", progress=30, status="running", started_at=datetime.now(timezone.utc))
            result, token_meta = await generate_profile(session, char_id, owner_id, task_id)
            if task_id and token_meta:
                usage_info = {"config_id": token_meta.get("config_id"), "model": token_meta.get("model"), "task_type": "character"}
                await task_service.update_task_progress(
                    task_id, stage="done", progress=100, status="success",
                    finished_at=datetime.now(timezone.utc),
                    tokens_in=token_meta.get("tokens_in", 0), tokens_out=token_meta.get("tokens_out", 0),
                    usage_info=usage_info,
                )
        except Exception as e:  # 后台任务异常不应影响主流程
            from common.task_errors import to_user_error
            logger.exception("人物小传生成失败 char=%s: %s", char_id, e)
            if task_id:
                if isinstance(e, task_cancel.TaskCancelled):
                    # 从异常中提取 usage_info（generate_profile 取消时已附加）
                    ui = getattr(e, "usage_info", None)
                    await task_service.update_task_progress(
                        task_id, stage="cancelled", status="cancelled",
                        error=e.reason, finished_at=datetime.now(timezone.utc),
                        tokens_in=e.tokens_in or 0, tokens_out=e.tokens_out or 0,
                        usage_info=ui,
                    )
                else:
                    await task_service.update_task_progress(task_id, stage="failed", status="failed", error=to_user_error(e), finished_at=datetime.now(timezone.utc))
            print(f"[character] 生成失败 char={char_id}: {e}")


# ---------------------------------------------------------------------------
# 批量人物分析（小说详情页「任务分析」按钮触发）
# ---------------------------------------------------------------------------
# M3 混合管道：先 nlp 确定性层出候选+出现次数，再 LLM 候选精析（见 _analyze_candidates）

# 单候选精析提示词与人物上下文抽取统一收敛到 common.nlp（见 nlp.CANDIDATE_PROMPT /
# nlp.extract_character_context），本模块直接复用，避免双份实现漂移（P1-9）。


async def _call_llm(messages, *, cfg):
    """经 LangChain 调用层发起 LLM 调用，返回 (文本, usage_dict)。
    usage 由调用方累积，任务结束时由 update_task_progress 统一写入，不逐次写 DB。

    关键点（V20 修复）：
        串行任务队列仅单 worker，单个 LLM 调用若因第三方端点挂起而无限阻塞，
        会饿死整条队列致使后续所有长任务卡在 pending。ChatOpenAI 的 timeout 在
        异步网络挂起场景下未必生效，故此处用 asyncio.wait_for 套一层硬超时兜底，
        超时即抛，由上层 try/except 捕获并回写任务失败，释放 worker。
    """
    import asyncio
    from llm.langchain_factory import get_langchain_model, invoke_with_usage, CHAT_TIMEOUT
    model = get_langchain_model(cfg)
    try:
        resp, usage = await asyncio.wait_for(
            invoke_with_usage(model, messages),
            timeout=CHAT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise BizError(504, f"大模型调用超时（>{int(CHAT_TIMEOUT)}s），请检查 LLM 配置的 base_url 与网络连通性")
    content = getattr(resp, "content", None)
    return (content if isinstance(content, str) else str(resp), usage)


async def _analyze_candidates(session, novel_id, owner_id, full_text, cands, async_task_id):
    """候选精析（M3 混合管道核心）：nlp 候选名单 → 每候选 LLM 精析 → 写 story_character。

    出现次数取 nlp 准确 freq（零成本、不依赖 LLM）；调用次数 = 候选数，
    远少于旧分片抽全类，落实「调用降 ≥50%」。无候选时不调用本函数（回退分片逻辑）。
    """
    cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
    if not cfgs:
        raise BizError(400, "尚未配置对话模型（llm_type=chat），请先在模型管理中添加")
    cfg = cfgs[0]
    config_id = cfg.id
    n = len(cands)
    created = skipped = 0
    tok_in = tok_out = 0
    for i, c in enumerate(cands):
        name = c["name"]
        freq = c.get("freq", 0)
        lo = 20 + int(i / n * 65)
        if async_task_id:
            await task_service.update_task_progress(
                async_task_id, stage="analyzing", progress=lo, status="running")
        if async_task_id and task_cancel.is_cancelled(async_task_id):
            await task_service.update_task_progress(async_task_id, tokens_in=tok_in, tokens_out=tok_out)
            exc = task_cancel.TaskCancelled("用户主动取消人物", tok_in, tok_out)
            exc.usage_info = {"config_id": config_id, "model": cfg.model}
            raise exc
        context = nlp.extract_character_context(full_text, name)
        prompt = nlp.CANDIDATE_PROMPT.replace("{name}", name).replace("{text}", context)
        messages = [{"role": "user", "content": prompt}]
        text, usage = await _call_llm(messages, cfg=cfg)
        if usage:
            tok_in += usage.get("tokens_in", 0)
            tok_out += usage.get("tokens_out", 0)
        data = _parse_json(text)
        item = data[0] if isinstance(data, list) else data
        if not isinstance(item, dict):
            continue
        existing = await character_repo.get_by_novel_name(session, novel_id, name)
        if existing:
            skipped += 1
            continue
        # 需求3兜底：全书出场次数（jieba 词频 freq）< 10 不入库
        if freq < 10:
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
            source="auto", appearances=freq,
        )
        session.add(c_obj)
        created += 1
    await session.commit()
    # token 用量由终态 update_task_progress 统一写入 story_llm_usage
    usage_info = {"config_id": config_id, "model": cfg.model, "task_type": "character_analysis"}
    if async_task_id:
        await task_service.update_task_progress(
            async_task_id, stage="done", progress=100, status="success",
            message=f"已创建 {created} 个人物" + (f"，跳过 {skipped} 个已存在" if skipped else ""),
            finished_at=datetime.now(timezone.utc),
            tokens_in=tok_in, tokens_out=tok_out, usage_info=usage_info,
            extra={"created": created, "skipped": skipped, "candidates": n})
    return {"created": created, "skipped": skipped, "candidates": n}


async def analyze_via_graph(session, novel_id, owner_id, full_text, cands, async_task_id):
    """M7 编排层入口：用 LangGraph 状态图执行人物分析（extract→analyze→persist）。

    与 M3 线性 _analyze_candidates 产出一致（候选精析 + nlp 准确 appearances），
    区别仅在于用状态图编排节点，便于后续扩展去重/质检/回退节点。
    """
    from graph.character_analysis_graph import build_graph
    cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
    if not cfgs:
        raise BizError(400, "尚未配置对话模型（llm_type=chat），请先在模型管理中添加")
    cfg = cfgs[0]
    config_id = cfg.id
    graph = build_graph()
    initial = {
        "novel_id": novel_id, "owner_id": owner_id, "full_text": full_text,
        "candidates": cands, "results": [], "created": 0, "skipped": 0, "failed": 0,
    }
    # 整体超时保护（P2-8）：避免单个候选 LLM 长尾/卡死导致整图无限等待；
    # 超时后 asyncio.CancelledError 透传，配合 _analyze_node 的逐候选兜底，
    # 已成功落库的人物不回滚（failed 计数可追溯血缘）。
    try:
        final = await asyncio.wait_for(
            graph.ainvoke(
                initial,
                config={"configurable": {
                    "session": session, "cfg": cfg, "config_id": config_id,
                    "task_id": async_task_id,
                    "thread_id": f"char_{async_task_id or novel_id}",
                }}),
            timeout=CHARACTER_GRAPH_TIMEOUT,
        )
    except asyncio.TimeoutError:
        # 超时视为分析未完成：已落库的保留，未完成的按 failed 记录血缘
        created = 0
        skipped = 0
        n = len(cands)
        if async_task_id:
            await task_service.update_task_progress(
                async_task_id, stage="failed", progress=100, status="failed",
                error=f"人物分析超时（>{CHARACTER_GRAPH_TIMEOUT}s），候选 {n} 个未全部完成",
                finished_at=datetime.now(timezone.utc),
                extra={"created": created, "skipped": skipped, "candidates": n, "failed": n})
        raise BizError(408, f"人物分析超时（>{CHARACTER_GRAPH_TIMEOUT}s），请减少正文量或稍后重试")
    created = final["created"]
    skipped = final["skipped"]
    n = len(final["candidates"])
    # 从 graph state 中提取累积的 token 用量，终态统一写入 story_llm_usage
    tok_in = final.get("tok_in", 0)
    tok_out = final.get("tok_out", 0)
    if async_task_id:
        usage_info = {"config_id": config_id, "model": cfg.model, "task_type": "character_analysis_graph"}
        await task_service.update_task_progress(
            async_task_id, stage="done", progress=100, status="success",
            message=f"已创建 {created} 个人物" + (f"，跳过 {skipped} 个已存在" if skipped else ""),
            finished_at=datetime.now(timezone.utc),
            tokens_in=tok_in, tokens_out=tok_out, usage_info=usage_info,
            extra={"created": created, "skipped": skipped, "candidates": n})
    return {"created": created, "skipped": skipped, "candidates": n}


_ANALYZE_PROMPT = """你是小说人物分析助手，请根据提供的小说简介和正文片段，识别小说中的主要人物并生成结构化档案。

请严格按以下 JSON 数组格式输出（只输出 JSON，不要包含任何解释或 markdown 标记）：
[
  {
    "name": "人物姓名",
    "role": "主角/配角/反派",
    "gender": "男/女/未知",
    "identity": "身份/职业",
    "personality": "性格特点",
    "appearance": "外貌特征",
    "catchphrase": "口头禅",
    "description": "150字以内的人物小传，包含大致经历"
  }
]

要求：
1. 只输出 JSON 数组，不要加 ```json 标记
2. 至少输出主角，最多输出 8 个主要人物
3. 如果小说正文中人物信息不足，可适当基于简介推断
4. description 控制在 150 字以内

小说名称：《{novel_name}》
小说简介：{summary}
小说正文片段：
{text}"""


async def analyze_and_create_characters(
    novel_id: int, owner_id: int, novel_name: str, summary: str,
    async_task_id: int | None = None,
    chapter_ids: list[int] | None = None,
) -> dict:
    """异步分析小说并自动创建人物档案（后台任务，支持长文分片 + 流式进度）。

    整体思路：
        取小说简介 + 章节全量正文 → 按字符窗口分片 → 逐片流式调 chat 适配器分析
        → 跨片按姓名去重合并 → 逐条写入人物卡。
    关键点：
        1. 独立 SessionLocal，不持有请求会话。
        2. 长文分片（每片携带小说简介作全局上下文），突破单 prompt 1 万字截断，覆盖完整正文。
        3. 流式调用逐片回写进度（20→50），避免等待期进度卡死。
        4. 同名人物理跳过（幂等）；跨片合并时补缺失字段、description 取最长。
        5. 写入 LLMUsage 记录累计 Token 用量供仪表盘统计。
    实现逻辑：
        拼接全量正文 → 分片 → 逐片 prompt → _stream_collect → _parse_json →
        _merge_characters → 逐条 create（跳过已存在）。
    """
    from db import SessionLocal
    async with SessionLocal() as session:
        try:
            if async_task_id:
                await task_service.update_task_progress(
                    async_task_id, stage="preparing", progress=5, status="running",
                    started_at=datetime.now(timezone.utc),
                )
            # 取章节正文：指定 chapter_ids 时仅取这些章节，否则取全量
            if chapter_ids:
                chapters = await novel_repo.list_chapters_by_ids(session, novel_id, chapter_ids)
                if not chapters:
                    raise BizError(400, "指定的章节不存在或不属于该小说")
            else:
                chapters = await novel_repo.list_all_chapters(session, novel_id)
                if not chapters:
                    raise BizError(400, "该小说暂无章节正文，请先上传并解析文档")
            full_text = "\n".join(ch.content for ch in chapters)
            # ── M3/M7 混合管道：确定性层优先（候选精析） ──
            # 先 nlp 零成本产出人物候选与准确出现次数，再 LLM 逐候选精析小传；
            # 调用次数=候选数（远少于旧分片抽全类），落实「调用降 ≥50%」。
            # 启用 LangGraph 编排层（M7）时走状态图路径，否则走 M3 线性路径。
            # 需求3：全书出场次数（jieba 词频 freq）< 10 的人物不入库。
            # 这里把确定性候选层的 min_freq 直接提到 10，freq<10 的候选根本不进入精析，
            # 从源头避免低频人物入库；_analyze_candidates 入库前再卡一次 freq<10 兜底。
            cands = nlp.extract_person_candidates(full_text, min_freq=nlp.MIN_FREQ_STRICT)
            if cands and LANGGRAPH_ENABLED:
                return await analyze_via_graph(session, novel_id, owner_id, full_text, cands, async_task_id)
            if cands:
                return await _analyze_candidates(
                    session, novel_id, owner_id, full_text, cands, async_task_id)
            # 无候选（确定性层漏召回）回退旧分片逻辑（保底）
            # 长文策略：按阅读顺序分片，每片在 prompt 中携带简介；主要人物多在前段，优先覆盖
            chunks = _chunk_text(full_text)
            n = len(chunks)
            adapter, model_name = await _get_chat_adapter(session, owner_id)
            cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
            config_id = cfgs[0].id if cfgs else None

            all_chars: list[dict] = []
            tok_in = tok_out = 0
            for i, chunk in enumerate(chunks):
                prompt = _ANALYZE_PROMPT.replace("{novel_name}", novel_name).replace(
                    "{summary}", summary or "暂无").replace("{text}", chunk)
                lo = 20 + int(i / n * 30)   # 进度区间：20 → 50
                hi = 20 + int((i + 1) / n * 30)
                if async_task_id:
                    await task_service.update_task_progress(
                        async_task_id, stage="analyzing", progress=lo, status="running",
                        tokens_in=tok_in, tokens_out=tok_out)
                raw = await _stream_collect(
                    adapter, [{"role": "user", "content": prompt}], async_task_id,
                    lo=lo, hi=hi, stage="analyzing", temperature=0.5, max_tokens=3000,
                )
                u = adapter.get_last_usage()
                if u:
                    tok_in += u.get("tokens_in", 0)
                    tok_out += u.get("tokens_out", 0)
                # 用户主动取消：当前分片已消耗 token，上报至异常由外层统一写入 DB
                if async_task_id and task_cancel.is_cancelled(async_task_id):
                    await task_service.update_task_progress(
                        async_task_id, tokens_in=tok_in, tokens_out=tok_out)
                    exc = task_cancel.TaskCancelled("用户主动取消人物", tok_in, tok_out)
                    exc.usage_info = {"config_id": config_id, "model": model_name}
                    raise exc
                chars_data = _parse_json(raw)
                if isinstance(chars_data, list):
                    all_chars.extend(chars_data)
                elif isinstance(chars_data, dict):
                    all_chars.append(chars_data)

            # 跨分片去重合并
            merged = _merge_characters(all_chars)

            if async_task_id:
                await task_service.update_task_progress(
                    async_task_id, stage="creating", progress=55, status="running",
                )

            created = 0
            skipped = 0
            for item in merged:
                name = (item.get("name") or "").strip()
                if not name:
                    continue
                # 同名人物跳过
                existing = await character_repo.get_by_novel_name(session, novel_id, name)
                if existing:
                    skipped += 1
                    continue
                # 需求3兜底（回退分片路径无 jieba freq，用全书字符串出现次数近似）：<10 不入库
                if full_text.count(name) < 10:
                    skipped += 1
                    continue
                c = Character(
                    novel_id=novel_id, owner_id=owner_id, name=name,
                    role=(item.get("role") or "配角").strip() or "配角",
                    gender=(item.get("gender") or "").strip() or None,
                    identity=(item.get("identity") or "").strip() or None,
                    personality=(item.get("personality") or "").strip() or None,
                    appearance=(item.get("appearance") or "").strip() or None,
                    catchphrase=(item.get("catchphrase") or "").strip() or None,
                    description=(item.get("description") or "").strip()[:150] or None,
                    source="auto",
                    appearances=0,
                )
                session.add(c)
                created += 1
            await session.commit()

            # token 用量由终态 update_task_progress 统一写入 story_llm_usage
            usage_info = {"config_id": config_id, "model": model_name, "task_type": "character_analysis"}

            # 收尾前再次确认是否被取消（避免取消请求晚于进度回写导致状态回退为成功）
            if async_task_id and task_cancel.is_cancelled(async_task_id):
                await task_service.update_task_progress(
                    async_task_id, tokens_in=tok_in, tokens_out=tok_out)
                exc = task_cancel.TaskCancelled("用户主动取消人物", tok_in, tok_out)
                exc.usage_info = usage_info
                raise exc

            if async_task_id:
                await task_service.update_task_progress(
                    async_task_id, stage="done", progress=100, status="success",
                    message=f"已创建 {created} 个人物" + (f"，跳过 {skipped} 个已存在" if skipped else ""),
                    finished_at=datetime.now(timezone.utc),
                    tokens_in=tok_in, tokens_out=tok_out, usage_info=usage_info,
                    extra={"created": created, "skipped": skipped, "chunks": n},
                )
            return {"created": created, "skipped": skipped, "chunks": n}
        except Exception as e:
            await session.rollback()
            logger.exception("人物分析失败: %s", e)
            from common.task_errors import to_user_error
            if async_task_id:
                if isinstance(e, task_cancel.TaskCancelled):
                    # 从异常中提取 usage_info（各取消检查点已附加），终态统一写入
                    ui = getattr(e, "usage_info", None)
                    await task_service.update_task_progress(
                        async_task_id, stage="cancelled", status="cancelled",
                        error=e.reason, finished_at=datetime.now(timezone.utc),
                        tokens_in=e.tokens_in or 0, tokens_out=e.tokens_out or 0,
                        usage_info=ui,
                    )
                    return {"created": 0, "skipped": 0, "cancelled": True, "chunks": n}
                await task_service.update_task_progress(
                    async_task_id, stage="failed", status="failed",
                    error=to_user_error(e), finished_at=datetime.now(timezone.utc),
                )
            raise

