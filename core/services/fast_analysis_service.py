"""极速模式分析服务（M-极速）。

整体思路：
    为「人物分析 / 章节解析 / 图谱抽取」三个入口提供极速模式实现：将小说全文切分为少量
    大块（由 config.TURBO_MAX_INPUT_CHARS 控制单次投喂量），用 1~N 次大上下文 LLM 调用
    产出散文/Markdown 摘要，多块则再合并一次，最终按 (novel_id, analysis_type) 存一份
    最新摘要到 story_analysis_summary，与深度模式的结构化库并存、互不影响。

关键点：
    1. 复用现有对话模型分发链（llm_repo.list_for_dispatch + llm_adapters.get_adapter），
       不引入新依赖；未配模型时抛 BizError，由上层任务标记为失败。
    2. 自开 SessionLocal，进度回写统一任务（与 run_extract 同范式），异常仅记录不抛出。
    3. 调用次数 = ceil(正文长度 / TURBO_MAX_INPUT_CHARS) +（多块时 1 次合并），
       远少于深度模式的 1330 次，故秒~分钟级出结果。

实现逻辑：
    组装全文 → 切块 → 逐块按类型 prompt 调 LLM → 多块合并 → upsert 摘要 → 回写任务进度。
"""
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select

from db import SessionLocal
from models.analysis_summary import StoryAnalysisSummary
from models.character import Character
from repositories import novel_repo, llm_repo, character_repo
from services import task_service
from llm import langchain_factory as llm_adapters
from common import crypto, task_cancel
from common.exceptions import BizError
from common.cache_adapter import default_cache
from config import TURBO_MAX_INPUT_CHARS

# 人物抽取投喂量：主角色通常在开篇登场，极速模式无需投喂全本，
# 限制为较小片段可显著缩短第三方中转的响应耗时，确保「极速」体验。
TURBO_CHAR_INPUT_CHARS = int(os.getenv("TURBO_CHAR_INPUT_CHARS", "20000"))

logger = logging.getLogger(__name__)


def _chunk_text(text: str, max_chars: int) -> list[str]:
    """将长文本按 max_chars 切块，块间留 300 字符重叠以保证摘要连贯。

    实现逻辑：
        顺序切片；若剩余不足 max_chars 直接收尾；重叠部分在下一刀起点回退 300。
    """
    if len(text) <= max_chars:
        return [text]
    overlap = 300
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _prompt_for(analysis_type: str, text: str):
    """按分析类型返回 (system_prompt, user_prompt)。"""
    if analysis_type == "character":
        sys_p = (
            "你是一位资深文学分析助手。阅读用户提供的「小说正文片段」，提取其中出现的全部人物"
            "（含主要角色与次要角色），为每个人物撰写一段【人物画像】，包含：姓名、身份/职业、"
            "外貌特征、性格特质、关键事迹或命运走向。用 Markdown 逐人列出，不要输出无关解释；"
            "若片段中无明显人物，输出「本片段暂无明显人物」。"
        )
    elif analysis_type == "chapter":
        sys_p = (
            "你是一位小说结构分析助手。阅读「小说正文片段」，提炼本段情节概览：核心目标、关键事件、"
            "转折点、主要人物动向。用 Markdown 列表呈现，简洁扼要，不要虚构内容。"
        )
    else:  # graph
        sys_p = (
            "你是一位知识图谱分析助手。阅读「小说正文片段」，抽取核心实体（人物/组织/地点/物品）"
            "及其相互关系，输出关键关系概览（实体A — 关系 — 实体B），用 Markdown 列表。"
            "仅基于片段内容，不虚构。"
        )
    usr_p = f"以下是小说正文片段：\n\n{text}"
    return sys_p, usr_p


def _merge_prompt(partials: list[str]):
    """多块摘要合并提示：去重、连贯、保留全部信息。"""
    sys_p = (
        "你是一位编辑。下面是一篇小说分多次生成的多段分析摘要，请将它们合并为一份连贯、"
        "去重的总摘要（保持 Markdown 格式），保留全部人物、情节与关系，去除重复项。"
    )
    usr_p = "\n\n===== 分段摘要 =====\n\n".join(partials)
    return sys_p, usr_p


def _usage_tokens(usage) -> tuple[int, int]:
    """归一化适配器返回的用量字典为 (input, output) 整数。"""
    if not usage or not isinstance(usage, dict):
        return 0, 0
    tin = usage.get("input") or usage.get("prompt_tokens") or 0
    tout = usage.get("output") or usage.get("completion_tokens") or 0
    try:
        return int(tin), int(tout)
    except (TypeError, ValueError):
        return 0, 0


async def _chat(session, owner_id: int, messages: list[dict], json_mode: bool = False, cache_key: str | None = None) -> tuple[str, object]:
    """取默认对话模型适配器并调用 chat，返回 (文本, 用量)。

    json_mode=True 时要求模型输出合法 JSON（ollama 走 format=json，其余走 response_format）。
    cache_key 非空且 ENABLE_LLM_CACHE 时，先查共享缓存命中则直接返回，未命中则调用后写回。
    """
    # 结果缓存：相同输入跳过 LLM 调用，省 token/耗时（多 worker 下走 Redis 共享）
    if cache_key:
        try:
            hit = default_cache.get(cache_key)
            if hit is not None:
                return hit, None
        except Exception:
            pass
    cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
    if not cfgs:
        raise BizError(400, "尚未配置对话模型（llm_type=chat），请先在模型管理中添加")
    cfg = cfgs[0]
    adapter = llm_adapters.get_adapter(cfg, crypto.decrypt(cfg.api_key))
    text = await adapter.chat(messages, json_mode=json_mode)
    if cache_key:
        try:
            default_cache.set(cache_key, text or "")
        except Exception:
            pass
    return text or "", adapter.get_last_usage()


def _as_list(data) -> list:
    """把 LLM 返回的 JSON（可能是数组，也可能被包成 {"key":[...]} 或单对象）归一为人物字典列表。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return v
        return [data]
    return []


# ── 极速人物分析：结构化抽取 + 写「人物档案」 ──────────────────────────────
# 阶段播报：正在解析小说(reading) → 正在归纳人物(char_extract)
#          → 正在总结人物经历(char_experience) → 正在保存到人物档案(char_save)
_CHAR_EXTRACT_SYS = (
    "你是一位资深文学分析助手。阅读用户提供的「小说正文片段」，识别其中出现的主要人物"
    "（主角与重要配角），为每人提取结构化信息。\n"
    "仅输出一个 JSON 数组，不要包含任何解释或 markdown 标记，格式严格如下：\n"
    "[{\"name\":\"姓名\",\"role\":\"主角/配角/反派\",\"gender\":\"男/女/未知\","
    "\"identity\":\"身份/职业\",\"personality\":\"性格特点\","
    "\"appearance\":\"外貌特征\",\"catchphrase\":\"口头禅\"}]\n"
    "要求：最多 8 个主要人物；若片段中无明显人物，输出 []。"
)
_CHAR_EXTRACT_USR = "以下是小说正文片段：\n\n{text}"

_CHAR_EXP_SYS = (
    "你是一位小说人物小传撰写助手。请基于小说正文，为给定人物名单中的每个人物撰写一段"
    "【人物经历小传】（150 字以内，包含大致经历与命运走向）。\n"
    "仅输出一个 JSON 数组，格式：[{\"name\":\"姓名\",\"description\":\"经历小传\"}]，"
    "不要包含任何解释或 markdown 标记。"
)
_CHAR_EXP_USR = "人物名单：{names}\n\n小说正文：\n{text}"

# 阶段4：提取关键事件（含章节范围），为人物归档追加结构化事件列表
_CHAR_EVENTS_SYS = (
    "你是一位小说事件分析助手。根据带章节标记的小说正文，为每个人物提取其关键事件。\n"
    "仅输出一个 JSON 数组，不要包含任何解释或 markdown 标记，格式严格如下：\n"
    "[{\"name\":\"人物名\",\"key_events\":[{\"event\":\"事件内容的概括描述\",\"chapters\":\"第X章-第Y章\"}]}]\n"
    "要求：每人最多 3 个关键事件；若该人物出场极少、无明显关键事件，则 key_events 为空数组 []。"
)
_CHAR_EVENTS_USR = "人物名单：{names}\n\n带章节标记的小说正文：\n{text}"


def _build_character_summary(chars: list[dict]) -> str:
    """由结构化人物列表生成 Markdown 摘要（供「人物摘要」按钮展示）。"""
    if not chars:
        return "本分析未识别到明显人物，请尝试深度模式或上传更完整正文。"
    lines = ["## 人物画像（极速分析）\n"]
    for c in chars:
        name = c.get("name") or "未知"
        role = c.get("role") or "配角"
        lines.append(f"### {name}（{role}）")
        for label, key in (("性别", "gender"), ("身份", "identity"),
                           ("性格", "personality"), ("外貌", "appearance"),
                           ("口头禅", "catchphrase")):
            v = (c.get(key) or "").strip()
            if v:
                lines.append(f"- {label}：{v}")
        desc = (c.get("description") or "").strip()
        if desc:
            lines.append(f"\n{desc}")
        lines.append("")
    return "\n".join(lines)


async def _run_character_turbo(session, novel_id, owner_id, full_text, task_id, chapters_text=""):
    """极速人物分析：抽取结构化人物并写入「人物档案」，实时播报 5 个阶段（含关键事件提取）。"""
    from services.character_service import _parse_json, _merge_characters

    # 阶段2：正在归纳人物
    if task_id:
        await task_service.update_task_progress(
            task_id, stage="char_extract", progress=10, status="running")
    chunks = _chunk_text(full_text[:TURBO_CHAR_INPUT_CHARS], TURBO_CHAR_INPUT_CHARS)
    n = len(chunks)
    tok_in = tok_out = 0
    raw_chars: list[dict] = []
    for i, ch in enumerate(chunks):
        text, usage = await _chat(
            session, owner_id,
            [{"role": "system", "content": _CHAR_EXTRACT_SYS},
             {"role": "user", "content": _CHAR_EXTRACT_USR.format(text=ch)}],
            json_mode=True,
            cache_key=f"turbo:{owner_id}:character_extract:{hash(ch)}")
        a, b = _usage_tokens(usage)
        tok_in += a
        tok_out += b
        raw_chars.extend(_as_list(_parse_json(text)))
        if task_id:
            await task_service.update_task_progress(
                task_id, stage="char_extract", progress=10 + int(35 * (i + 1) / n))
        if task_id and task_cancel.is_cancelled(task_id):
            await task_service.update_task_progress(
                task_id, tokens_in=tok_in, tokens_out=tok_out)
            exc = task_cancel.TaskCancelled("用户主动取消人物", tok_in, tok_out)
            exc.usage_info = {"config_id": None, "model": "turbo"}
            raise exc

    merged = _merge_characters(raw_chars)
    names = [c.get("name") for c in merged if (c.get("name") or "").strip()]

    # 阶段3：正在总结人物经历
    if task_id:
        await task_service.update_task_progress(
            task_id, stage="char_experience", progress=50, status="running")
    if names:
        exp_text, usage = await _chat(
            session, owner_id,
            [{"role": "system", "content": _CHAR_EXP_SYS},
             {"role": "user", "content": _CHAR_EXP_USR.format(
                 names="、".join(names), text=full_text[:TURBO_CHAR_INPUT_CHARS])}],
            json_mode=True,
            cache_key=f"turbo:{owner_id}:character_exp:{hash(names)}:{hash(full_text[:TURBO_CHAR_INPUT_CHARS])}")
        a, b = _usage_tokens(usage)
        tok_in += a
        tok_out += b
        exp_items = _as_list(_parse_json(exp_text))
        exp_map = {}
        for it in exp_items:
            nm = (it.get("name") or "").strip()
            if nm and (it.get("description") or "").strip():
                exp_map[nm] = it["description"].strip()
        for c in merged:
            d = exp_map.get((c.get("name") or "").strip())
            if d:
                c["description"] = d[:150]
    if task_id:
        await task_service.update_task_progress(
            task_id, stage="char_experience", progress=70)

    # 阶段4：提取关键事件（含章节范围），为人物归档追加结构化事件列表
    if task_id:
        await task_service.update_task_progress(
            task_id, stage="char_events", progress=72, status="running")
    events_map: dict[str, list] = {}
    if names and chapters_text:
        ev_text, usage = await _chat(
            session, owner_id,
            [{"role": "system", "content": _CHAR_EVENTS_SYS},
             {"role": "user", "content": _CHAR_EVENTS_USR.format(
                 names="、".join(names), text=chapters_text[:TURBO_CHAR_INPUT_CHARS])}],
            json_mode=True,
            cache_key=f"turbo:{owner_id}:character_events:{hash(names)}:{hash(chapters_text[:TURBO_CHAR_INPUT_CHARS])}")
        a, b = _usage_tokens(usage)
        tok_in += a
        tok_out += b
        for it in _as_list(_parse_json(ev_text)):
            nm = (it.get("name") or "").strip()
            ke = it.get("key_events")
            if nm and isinstance(ke, list) and ke:
                events_map[nm] = ke
    if task_id:
        await task_service.update_task_progress(
            task_id, stage="char_events", progress=74)

    # 阶段5：正在保存到人物档案
    if task_id:
        await task_service.update_task_progress(
            task_id, stage="char_save", progress=75, status="running")
    created = skipped = 0
    for c in merged:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        existing = await character_repo.get_by_novel_name(session, novel_id, name)
        if existing:
            skipped += 1
            continue
        session.add(Character(
            novel_id=novel_id, owner_id=owner_id, name=name,
            role=(c.get("role") or "配角").strip() or "配角",
            gender=(c.get("gender") or "").strip() or None,
            identity=(c.get("identity") or "").strip() or None,
            personality=(c.get("personality") or "").strip() or None,
            appearance=(c.get("appearance") or "").strip() or None,
            catchphrase=(c.get("catchphrase") or "").strip() or None,
            description=(c.get("description") or "").strip()[:150] or None,
            source="auto", appearances=full_text.count(name),
            extra={"key_events": events_map.get(name, [])}))
        created += 1
    await session.commit()

    # 写摘要（供「人物摘要」按钮）
    summary = _build_character_summary(merged)
    existing = (await session.execute(
        select(StoryAnalysisSummary).where(
            StoryAnalysisSummary.novel_id == novel_id,
            StoryAnalysisSummary.analysis_type == "character"))).scalars().first()
    if existing:
        existing.content = summary
        existing.updated_at = datetime.now(timezone.utc)
    else:
        session.add(StoryAnalysisSummary(
            novel_id=novel_id, owner_id=owner_id,
            analysis_type="character", mode="turbo",
            content=summary, fmt="markdown"))
    await session.commit()

    # token 用量由终态 update_task_progress 统一写入 story_llm_usage
    usage_info = {"config_id": None, "model": "turbo", "task_type": "turbo_character"}

    if task_id:
        msg = f"已创建 {created} 个人物档案"
        if skipped:
            msg += f"，跳过 {skipped} 个已存在"
        if not merged:
            msg = "未识别到明显人物，可尝试深度模式或上传更完整正文"
        await task_service.update_task_progress(
            task_id, stage="done", progress=100, status="success",
            finished_at=datetime.now(timezone.utc),
            tokens_in=tok_in, tokens_out=tok_out, usage_info=usage_info,
            summary_chars=len(summary), message=msg)
    print(f"[turbo] character 完成 novel={novel_id} chars={len(merged)} created={created} skipped={skipped}")


async def run_turbo(
    novel_id: int, owner_id: int, analysis_type: str, task_id: int | None = None
) -> None:
    """极速模式后台入口：自开会话产出摘要并回写任务进度，异常仅记录不抛出。

    参数：
        analysis_type: character / chapter / graph
        task_id: 统一异步任务 id（由路由创建后传入），用于进度回写。
    """
    async with SessionLocal() as session:
        try:
            if task_id and task_cancel.is_cancelled(task_id):
                await task_service.update_task_progress(
                    task_id, stage="cancelled", status="cancelled",
                    error="用户主动取消任务", finished_at=datetime.now(timezone.utc))
                return
            if task_id:
                await task_service.update_task_progress(
                    task_id, stage="reading", progress=5, status="running",
                    started_at=datetime.now(timezone.utc))

            chapters = await novel_repo.list_all_chapters(session, novel_id)
            full_text = "\n".join((c.content or "") for c in chapters).strip()
            if not full_text:
                raise BizError(400, "小说暂无正文，无法极速分析（请先解析/上传小说）")

            # 人物分析：极速模式现在也会建立结构化「人物档案」，并实时播报 5 个阶段（含关键事件）
            if analysis_type == "character":
                # 构建章节标记文本，供 char_events 阶段识别章节范围
                marked_parts = [f"【第{i+1}章 {ch.title or ''}】\n{ch.content or ''}" for i, ch in enumerate(chapters)]
                chapters_text = "\n".join(marked_parts)
                await _run_character_turbo(session, novel_id, owner_id, full_text, task_id, chapters_text)
                return

            if task_id:
                await task_service.update_task_progress(task_id, stage="summarizing", progress=30)

            chunks = _chunk_text(full_text, TURBO_MAX_INPUT_CHARS)
            partials: list[str] = []
            tok_in = tok_out = 0
            n = len(chunks)
            for i, ch in enumerate(chunks):
                sys_p, usr_p = _prompt_for(analysis_type, ch)
                text, usage = await _chat(
                    session, owner_id,
                    [{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}],
                    cache_key=f"turbo:{owner_id}:{analysis_type}:{hash(ch)}")
                partials.append(text)
                a, b = _usage_tokens(usage)
                tok_in += a
                tok_out += b
                if task_id:
                    await task_service.update_task_progress(
                        task_id, progress=30 + int(50 * (i + 1) / n))

            if len(partials) == 1:
                summary = partials[0]
            else:
                sys_p, usr_p = _merge_prompt(partials)
                summary, usage = await _chat(
                    session, owner_id,
                    [{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}],
                    cache_key=f"turbo:{owner_id}:{analysis_type}:merge:{hash(tuple(partials))}")
                a, b = _usage_tokens(usage)
                tok_in += a
                tok_out += b

            # 按 (novel_id, analysis_type) upsert 一份最新摘要
            existing = (await session.execute(
                select(StoryAnalysisSummary).where(
                    StoryAnalysisSummary.novel_id == novel_id,
                    StoryAnalysisSummary.analysis_type == analysis_type))).scalars().first()
            if existing:
                existing.content = summary
                existing.updated_at = datetime.now(timezone.utc)
            else:
                session.add(StoryAnalysisSummary(
                    novel_id=novel_id, owner_id=owner_id,
                    analysis_type=analysis_type, mode="turbo",
                    content=summary, fmt="markdown"))
            await session.commit()

            # token 用量由终态 update_task_progress 统一写入 story_llm_usage
            usage_info = {"config_id": None, "model": "turbo", "task_type": f"turbo_{analysis_type}"}

            if task_id:
                await task_service.update_task_progress(
                    task_id, stage="done", progress=100, status="success",
                    finished_at=datetime.now(timezone.utc),
                    tokens_in=tok_in, tokens_out=tok_out, usage_info=usage_info,
                    summary_chars=len(summary))
            print(f"[turbo] {analysis_type} 完成 novel={novel_id} chunks={n} chars={len(summary)}")
        except Exception as e:
            if task_id:
                if isinstance(e, task_cancel.TaskCancelled):
                    ui = getattr(e, "usage_info", None)
                    await task_service.update_task_progress(
                        task_id, stage="cancelled", status="cancelled",
                        error=e.reason, finished_at=datetime.now(timezone.utc),
                        tokens_in=e.tokens_in or 0, tokens_out=e.tokens_out or 0,
                        usage_info=ui)
                else:
                    await task_service.update_task_progress(
                        task_id, stage="failed", status="failed",
                        error=str(e), finished_at=datetime.now(timezone.utc))
            print(f"[turbo] {analysis_type} 失败 novel={novel_id}: {e}")
