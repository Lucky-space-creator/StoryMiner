"""
章节解析业务逻辑（LLM 驱动的小说章节结构化分析）- v2 优化版

整体思路：
    分两阶段：Phase1 分析文档整体结构（类型/目录/卷/切分质量），
    Phase2 逐章单独发送 LLM 分析（每章独立请求），避免批量导致混淆。
    每章分析时携带小说全局背景（简介+前后章标题），提升上下文连续性。

关键点：
    1. Phase2 改为单章分析，每章独立 LLM 调用，max_tokens 充足不会截断。
    2. 首章特殊处理：检测是否为目录/前言/作者声明等非正文。
    3. 结果匹配不再依赖数组索引，改为按 chapter_index 精确定位。
    4. 每章 prompt 包含小说简介 + 全局章节导航，LLM 有上下文理解。
    5. 并发度控制（最多 3 个并发），避免 LLM API 速率限制。

实现逻辑：
    Phase1 结构分析 → Phase2 逐章并发分析 → 按 chapter_index 回写 extra.analysis
"""
import json
import logging
import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from models.novel_content import Chapter
from repositories import novel_repo, llm_repo
from services import llm_adapters, task_service
from common import crypto
from common import task_cancel
from common.exceptions import BizError

logger = logging.getLogger(__name__)

# 单章内容截断字数（保留足够上下文又不过长）
_CHAPTER_MAX_CHARS = 4000
# 并发控制：同时最多 N 个 LLM 请求
_MAX_CONCURRENCY = 3


# ============================================================================
# Phase 1: 整体文档结构分析 Prompt
# ============================================================================
_STRUCTURE_SYSTEM = """你是一位资深的小说编辑与出版专家，擅长分析小说的文档结构与章节组织。"""

_STRUCTURE_USER = """请分析以下小说文档的整体结构。

【基本信息】
小说名称：《{novel_name}》
用户提供的简介：{summary}
数据库中的章节总数：{total_chapters}

【全部章节标题列表】
{title_list}

【前3章内容摘要（供判断是否为目录/前言）】
{first_chapters_preview}

【分析任务】
请从以下维度分析该小说的文档结构，输出严格 JSON：

1. document_type - 文档类型判断：
   - "full_novel"：完整小说（包含从开头到结尾的全部章节）
   - "partial_volume"：部分卷/篇（只包含小说的某一部分，如"第一卷 第1-100章"）
   - "has_toc"：文档开头包含目录/章节索引（前几章可能是TOC而非正文）
   - "has_preface"：文档包含前言/作者序言/简介等非正文开头

2. toc_chapters - 如果检测到目录章节，列出其 chapter_no 数组（如 [1, 2]），否则 []

3. volumes - 分卷/分篇结构（从标题中识别"第X卷"、"第X篇"、"上/中/下册"等）：
   [{{"title": "卷名", "chapter_range": "1-50", "start_chapter_no": 1, "end_chapter_no": 50}}]
   如果无分卷结构则返回空数组。

4. chapter_quality - 章节切分质量评估：
   - "good"：切分合理，标题与内容匹配
   - "title_issues"：部分章节标题不准确或缺失
   - "merge_needed"：部分章节被过度切分（一个自然章被切成多段）
   - "has_toc_mixed"：目录/非正文内容混入了正文章节

5. prologue_chapter - 序章/楔子的 chapter_no（如第0章或标记为"楔子"的章节），无则为 null

6. epilogue_start - 尾声/后记开始的 chapter_no，无则为 null

7. narrative_pov - 叙事视角："first_person"/"third_person"/"mixed"

8. overall_structure - 200字以内的整体结构描述

【输出格式】
只输出一个 JSON 对象，不要 markdown 标记，不要解释文字。"""


# ============================================================================
# Phase 2: 单章分析 Prompt（每章独立发送）
# ============================================================================
_CHAPTER_SYSTEM = """你是一位资深的小说内容分析专家，擅长对小说章节进行结构化解析。"""

_CHAPTER_USER = """请分析以下小说章节。

【小说全局信息】
小说名称：《{novel_name}》
小说简介：{novel_summary}
总章节数：{total_chapters}

【章节导航】
上一章：{prev_title}
当前分析：第 {chapter_index} 章 / 共 {total_chapters} 章
标题：{chapter_title}
下一章：{next_title}

【章节正文】
{chapter_content}

【分析任务】
首先判断本章内容类型：
- 如果本章是目录/章节索引（大量"第X章 XXX"格式的行），输出 type: "toc"，其余字段可为空。
- 如果本章是前言/作者声明/简介/上架感言，输出 type: "preface"，summary 简短说明即可。
- 如果本章是正常正文，输出 type: "content"，完整分析。

然后对正文输出以下 JSON：
{{
  "chapter_index": {chapter_index},
  "chapter_title": "{chapter_title}",
  "type": "content",
  "summary": "80字以内的本章核心情节摘要，不能是模板套话",
  "key_events": ["本章发生的2-4个关键情节事件"],
  "characters_appeared": ["本章新出场或主要活动的人物（最多5个）"],
  "locations": ["本章涉及的场景地点（最多3个）"],
  "plot_role": "本章在整体剧情中的作用：开篇铺垫/日常过渡/冲突爆发/高潮/转折/收尾/伏笔埋设",
  "emotional_tone": "整体情绪基调：轻松/紧张/悲情/热血/温馨/悬疑/恐怖/平淡",
  "climax_sentence": "本章最精彩或最关键的一句话（可选）",
  "connections": "与前后章节的承接关系（如：承接上章XX事件，为下章YY伏笔）"
}}

【严格要求】
1. 只输出一个 JSON 对象，不要数组、不要 markdown 标记、不要解释。
2. chapter_index 必须为 {chapter_index}，chapter_title 必须为 "{chapter_title}"。
3. summary 必须基于实际内容编写，禁止使用"本章讲述了主角的冒险故事"等通用模板。
4. 如果内容过短（<100字），summary 写"本章内容过短，疑似切分异常"。 """


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _truncate(text: str, max_chars: int = _CHAPTER_MAX_CHARS) -> str:
    """截断文本到指定字数（完整句子边界）。"""
    if not text or len(text) <= max_chars:
        return text or ""
    # 优先在句号处截断
    for sep in ("。", "！", "？", "\n\n", "\n", "，", " "):
        cut = text.rfind(sep, 0, max_chars)
        if cut > max_chars * 0.5:
            return text[:cut + 1]
    return text[:max_chars]


def _sanitize_title(title: str | None, chapter_no: int) -> str:
    """规范化章节标题，去除可能混入的换行/多余空白/NUL字符。"""
    if not title:
        return f"第{chapter_no}章"
    # 去除NUL、换行、首尾空白，限制长度
    t = title.replace("\x00", "").replace("\n", " ").replace("\r", " ").strip()
    return t[:200] if t else f"第{chapter_no}章"


async def _get_chat_adapter(session: AsyncSession, owner_id: int):
    """取默认对话模型适配器，返回 (adapter, model_name, config_id)。"""
    cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
    if not cfgs:
        raise BizError(400, "尚未配置对话模型（llm_type=chat），请先在模型管理中添加")
    cfg = cfgs[0]
    adapter = llm_adapters.get_adapter(cfg, crypto.decrypt(cfg.api_key))
    return adapter, cfg.model, cfg.id


def _parse_json(raw) -> dict | list:
    """容错解析 LLM 输出的 JSON。"""
    if isinstance(raw, list):
        raw = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in raw)
    raw = (raw or "").strip()
    # 去 markdown 包裹
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    # 截取 JSON 部分
    arr_start, arr_end = raw.find("["), raw.rfind("]")
    obj_start, obj_end = raw.find("{"), raw.rfind("}")
    if arr_start != -1 and (obj_start == -1 or arr_start < obj_start):
        raw = raw[arr_start:arr_end + 1]
    elif obj_start != -1 and obj_end != -1:
        raw = raw[obj_start:obj_end + 1]
    try:
        return json.loads(raw)
    except Exception:
        return {}


async def _chat_once(adapter, system: str, user: str, task_id: int | None,
                     lo: int, hi: int, stage: str, temperature=0.3, max_tokens=2000) -> str:
    """单次非流式 chat 调用（超时自动重试一次）。"""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_error = None
    for attempt in (1, 2):
        try:
            result = await adapter.chat(messages, temperature=temperature, max_tokens=max_tokens)
            if task_id:
                await task_service.update_task_progress(task_id, stage=stage, progress=hi, status="running")
            return result or ""
        except Exception as e:
            last_error = e
            err_str = str(e)
            # 超时类异常重试一次
            if attempt == 1 and any(k in err_str for k in ("Timeout", "timeout", "ReadTimeout", "ConnectError")):
                logger.warning("LLM 调用超时 stage=%s attempt=%d，1秒后重试...", stage, attempt)
                await asyncio.sleep(1.5)
                continue
            raise
    raise last_error  # type: ignore[misc]


# ============================================================================
# Phase 1: 整体文档结构分析
# ============================================================================
async def _phase1_analyze_structure(
    session: AsyncSession, owner_id: int, novel_id: int, novel_name: str, summary: str,
    adapter, model_name: str, config_id: int, task_id: int | None,
) -> dict:
    """Phase 1：分析文档整体结构（类型/目录/分卷/切分质量）。"""
    chapters: list[Chapter] = await novel_repo.list_all_chapters(session, novel_id)
    total = len(chapters)
    if not chapters:
        return {"document_type": "empty", "toc_chapters": [], "volumes": [], "chapter_quality": "unknown"}

    # 构建标题列表（最多取100条，超长时取首尾各50 + 中间摘要）
    _MAX_TITLES = 100
    title_lines = []
    if total <= _MAX_TITLES:
        for i, c in enumerate(chapters):
            t = _sanitize_title(c.title, c.chapter_no)
            title_lines.append(f"第{i+1}条(chapter_no={c.chapter_no})：{t}")
    else:
        # 超长：取前60条 + 后40条，中间省略
        for i, c in enumerate(chapters[:60]):
            t = _sanitize_title(c.title, c.chapter_no)
            title_lines.append(f"第{i+1}条(chapter_no={c.chapter_no})：{t}")
        title_lines.append(f"...（中间省略 {total - 100} 章）...")
        for i, c in enumerate(chapters[-40:]):
            t = _sanitize_title(c.title, c.chapter_no)
            title_lines.append(f"第{total - 39 + i}条(chapter_no={c.chapter_no})：{t}")

    # 前3章内容预览（每章前300字，用于判断是否为目录/前言）
    preview_parts = []
    for i, c in enumerate(chapters[:3]):
        t = _sanitize_title(c.title, c.chapter_no)
        snippet = (c.content or "")[:300]
        preview_parts.append(f"---第{i+1}条(chapter_no={c.chapter_no})《{t}》前300字---\n{snippet}")

    user_prompt = _STRUCTURE_USER \
        .replace("{novel_name}", novel_name) \
        .replace("{summary}", summary or "无") \
        .replace("{total_chapters}", str(total)) \
        .replace("{title_list}", "\n".join(title_lines)) \
        .replace("{first_chapters_preview}", "\n\n".join(preview_parts))

    if task_id:
        await task_service.update_task_progress(task_id, stage="structure", progress=3, status="running")

    raw = await _chat_once(
        adapter, _STRUCTURE_SYSTEM, user_prompt, task_id,
        lo=3, hi=15, stage="structure", temperature=0.3, max_tokens=2500,
    )
    result = _parse_json(raw)
    if not isinstance(result, dict):
        result = {"overall_structure": str(raw)[:300]}

    # 确保必要字段存在
    result.setdefault("document_type", "full_novel")
    result.setdefault("toc_chapters", [])
    result.setdefault("volumes", [])
    result.setdefault("chapter_quality", "good")
    result.setdefault("prologue_chapter", None)
    result.setdefault("epilogue_start", None)
    result.setdefault("narrative_pov", "third_person")
    result.setdefault("total_chapters_in_doc", total)
    return result


# ============================================================================
# Phase 2: 逐章单独分析（并发控制）
# ============================================================================
async def _analyze_single_chapter(
    ch: Chapter, novel_name: str, novel_summary: str, total_chapters: int,
    chapters: list[Chapter], adapter, task_id: int | None,
    semaphore: asyncio.Semaphore, progress_start: int, progress_end: int,
) -> tuple[int, dict | None]:
    """分析单个章节，返回 (chapter_db_id, analysis_dict_or_None)。

    关键点：
        - 使用信号量控制并发数
        - 携带前后章标题作上下文导航
        - 按 chapter_index 精确定位返回结果
    """
    async with semaphore:
        ch_title = _sanitize_title(ch.title, ch.chapter_no)
        # 计算全局序号（第几章/共几章）
        chapter_index = ch.chapter_no  # 使用 DB 中的 chapter_no
        # 找到在列表中的位置
        pos = next((i for i, c in enumerate(chapters) if c.id == ch.id), 0)

        # 前后章标题
        prev_title = _sanitize_title(chapters[pos - 1].title, chapters[pos - 1].chapter_no) if pos > 0 else "（无，这是第一章）"
        next_title = _sanitize_title(chapters[pos + 1].title, chapters[pos + 1].chapter_no) if pos < len(chapters) - 1 else "（无，这是最后一章）"

        ch_content = _truncate(ch.content or "", _CHAPTER_MAX_CHARS)

        # 检查取消
        if task_id and task_cancel.is_cancelled(task_id):
            raise task_cancel.TaskCancelled("用户主动取消章节解析", 0, 0)

        user_prompt = _CHAPTER_USER \
            .replace("{novel_name}", novel_name) \
            .replace("{novel_summary}", novel_summary or "无") \
            .replace("{total_chapters}", str(total_chapters)) \
            .replace("{chapter_index}", str(chapter_index)) \
            .replace("{chapter_title}", ch_title) \
            .replace("{prev_title}", prev_title) \
            .replace("{next_title}", next_title) \
            .replace("{chapter_content}", ch_content)

        # 进度内插
        prog = progress_start + int((pos / max(total_chapters, 1)) * (progress_end - progress_start))
        if task_id:
            await task_service.update_task_progress(task_id, stage="analyzing", progress=prog, status="running")

        try:
            raw = await _chat_once(
                adapter, _CHAPTER_SYSTEM, user_prompt, task_id,
                lo=prog, hi=min(prog + 1, progress_end),
                stage="analyzing", temperature=0.4, max_tokens=1200,
            )
        except Exception as e:
            logger.warning("章节分析失败 chapter_no=%d id=%d: %s", chapter_index, ch.id, e)
            return ch.id, None

        result = _parse_json(raw)
        if not isinstance(result, dict):
            logger.warning("章节分析返回非JSON chapter_no=%d raw=%s", chapter_index, str(raw)[:200])
            return ch.id, None

        # 验证一致性
        returned_index = result.get("chapter_index")
        returned_title = result.get("chapter_title", "")
        if returned_index is not None and int(returned_index) != chapter_index:
            logger.warning("章节分析 index 不匹配: 期望%d, 返回%d, title=%s",
                           chapter_index, returned_index, ch_title)

        return ch.id, result


# ============================================================================
# 主入口：两阶段章节解析
# ============================================================================
async def analyze_chapters(
    novel_id: int, owner_id: int, novel_name: str, summary: str,
    async_task_id: int | None = None,
) -> dict:
    """异步分析小说章节（两阶段 LLM 分析）。

    Phase1：文档结构分析（检测类型/目录/分卷/切分质量）。
    Phase2：逐章并发分析（每章独立 LLM 调用，信号量控制并发）。

    返回：{"structure": {...}, "analyzed": N, "failed": N, "total": N}
    """
    from db import SessionLocal
    async with SessionLocal() as session:
        tok_in_total = 0
        tok_out_total = 0
        try:
            if async_task_id:
                await task_service.update_task_progress(
                    async_task_id, stage="preparing", progress=0, status="running",
                    started_at=datetime.now(timezone.utc),
                )

            chapters: list[Chapter] = await novel_repo.list_all_chapters(session, novel_id)
            total = len(chapters)
            if not chapters:
                raise BizError(400, "该小说暂无章节，请先上传并解析文档")

            adapter, model_name, config_id = await _get_chat_adapter(session, owner_id)

            # ---- Phase 1: 结构分析 ----
            if async_task_id:
                await task_service.update_task_progress(task_id=async_task_id, stage="structure", progress=2, status="running")

            structure = await _phase1_analyze_structure(
                session, owner_id, novel_id, novel_name, summary,
                adapter, model_name, config_id, async_task_id,
            )

            # 记录 Phase1 token
            u1 = adapter.get_last_usage()
            if u1:
                tok_in_total += u1.get("tokens_in", 0)
                tok_out_total += u1.get("tokens_out", 0)

            # 将结构分析存入第一章节（跳过目录章节）
            first_real_ch = chapters[0]
            toc_nos = set(structure.get("toc_chapters", []) or [])
            for c in chapters:
                if c.chapter_no not in toc_nos:
                    first_real_ch = c
                    break
            first_extra = first_real_ch.extra or {}
            first_extra["structure"] = structure
            first_real_ch.extra = first_extra
            await session.commit()

            # ---- Phase 2: 逐章分析（串行，逐一执行，不并发） ----
            if async_task_id:
                await task_service.update_task_progress(task_id=async_task_id, stage="analyzing", progress=15, status="running")

            # 过滤掉被识别为目录/前言的非正文章节（但仍标记为"已跳过"）
            skip_nos = set(structure.get("toc_chapters", []) or [])
            analyzable = [c for c in chapters if c.chapter_no not in skip_nos]

            # 串行执行：单章顺序调用 LLM，避免并发限流与进度混乱。
            # 复用 _analyze_single_chapter 的信号量参数，传 Semaphore(1) 即为串行占位。
            _seq_sem = asyncio.Semaphore(1)
            results: dict[int, dict] = {}  # chapter_db_id -> analysis
            analyzed = 0
            failed = 0
            cancelled = False

            for ch in analyzable:
                # 循环边界感知取消：用户取消后立即停止后续章节
                if async_task_id and task_cancel.is_cancelled(async_task_id):
                    cancelled = True
                    break
                try:
                    ch_id, data = await _analyze_single_chapter(
                        ch, novel_name, summary or "", total, chapters,
                        adapter, async_task_id, _seq_sem,
                        progress_start=15, progress_end=95,
                    )
                    if data is not None:
                        results[ch_id] = data
                        analyzed += 1
                    else:
                        failed += 1
                except task_cancel.TaskCancelled:
                    cancelled = True
                    break
                except Exception as e:
                    logger.warning("章节分析任务异常: %s", e)
                    failed += 1

            if cancelled:
                raise task_cancel.TaskCancelled("用户主动取消章节解析", tok_in_total, tok_out_total)

            # 累计 Phase2 token
            u2 = adapter.get_last_usage()
            if u2:
                tok_in_total += u2.get("tokens_in", 0) * (analyzed + failed)
                tok_out_total += u2.get("tokens_out", 0) * (analyzed + failed)

            # ---- 回写分析结果 ----
            if async_task_id:
                await task_service.update_task_progress(task_id=async_task_id, stage="saving", progress=95, status="running")

            # 构建 title→analysis 的查找表（用于跳过章节的标记）
            analyzed_ids = set(results.keys())

            for ch in chapters:
                ch_id = ch.id
                if ch_id in analyzed_ids:
                    data = results[ch_id]
                    current_extra = ch.extra or {}
                    current_extra["analysis"] = {
                        "type": data.get("type", "content"),
                        "summary": (data.get("summary") or "")[:120],
                        "key_events": data.get("key_events") or [],
                        "characters_appeared": data.get("characters_appeared") or [],
                        "locations": data.get("locations") or [],
                        "plot_role": data.get("plot_role") or "过渡",
                        "emotional_tone": data.get("emotional_tone") or "平淡",
                        "climax_sentence": data.get("climax_sentence") or "",
                        "connections": data.get("connections") or "",
                        "analyzed_at": datetime.now(timezone.utc).isoformat(),
                    }
                    ch.extra = current_extra

            # 对跳过的目录章节打标记
            for ch in chapters:
                if ch.chapter_no in skip_nos and ch.id not in analyzed_ids:
                    current_extra = ch.extra or {}
                    current_extra["analysis"] = {
                        "type": "toc",
                        "summary": "（目录/索引章节，已自动跳过分析）",
                        "key_events": [],
                        "characters_appeared": [],
                        "locations": [],
                        "plot_role": "目录",
                        "emotional_tone": "",
                        "analyzed_at": datetime.now(timezone.utc).isoformat(),
                    }
                    ch.extra = current_extra

            await session.commit()

            # 记录 token（用量记录失败不影响主流程）
            if tok_in_total or tok_out_total:
                try:
                    await task_service.record_llm_usage(
                        owner_id=owner_id, config_id=config_id, model=model_name,
                        task_type="chapter_analysis", tokens_in=tok_in_total, tokens_out=tok_out_total,
                    )
                except Exception:
                    pass
                if async_task_id:
                    await task_service.update_task_progress(
                        async_task_id, tokens_in=tok_in_total, tokens_out=tok_out_total)

            if async_task_id:
                skipped_info = f"，跳过{len(skip_nos)}章非正文" if skip_nos else ""
                await task_service.update_task_progress(
                    async_task_id, stage="done", progress=100, status="success",
                    message=f"已完成 {analyzed} 章分析，{failed} 章失败{skipped_info}",
                    finished_at=datetime.now(timezone.utc),
                    extra={"structure": structure, "analyzed": analyzed, "failed": failed,
                           "total": total, "skipped": len(skip_nos)},
                )

            logger.info("章节解析完成 novel=%s analyzed=%d/%d failed=%d skipped=%d",
                        novel_id, analyzed, total, failed, len(skip_nos))
            return {"structure": structure, "analyzed": analyzed, "failed": failed,
                    "total": total, "skipped": len(skip_nos)}

        except Exception as e:
            from common.task_errors import to_user_error
            await session.rollback()
            logger.exception("章节解析失败 novel=%s: %s", novel_id, e)
            if async_task_id:
                if isinstance(e, task_cancel.TaskCancelled):
                    await task_service.update_task_progress(
                        async_task_id, stage="cancelled", status="cancelled",
                        error=e.reason, finished_at=datetime.now(timezone.utc),
                        tokens_in=e.tokens_in, tokens_out=e.tokens_out,
                    )
                    return {"analyzed": 0, "failed": 0, "total": len(chapters) if 'chapters' in dir() else 0, "cancelled": True}
                await task_service.update_task_progress(
                    async_task_id, stage="failed", status="failed",
                    error=to_user_error(e), finished_at=datetime.now(timezone.utc),
                )
            raise


# ============================================================================
# 查询章节分析结果
# ============================================================================
async def get_chapter_analysis(session: AsyncSession, owner_id: int, novel_id: int) -> dict:
    """获取小说的章节分析结果（整体结构 + 各章分析摘要）。
    
    关键点：按 chapter_no 去重，每个章节号只保留一条（优先保留有分析结果的）。
    """
    novel = await novel_repo.get_novel(session, owner_id, novel_id)
    if not novel:
        raise BizError(404, "小说不存在")

    chapters: list[Chapter] = await novel_repo.list_all_chapters(session, novel_id)
    structure = None

    # 按 chapter_no 去重：每个章节号只保留一条，优先保留有分析数据的记录
    seen: dict[int, dict] = {}
    for ch in chapters:
        extra = ch.extra or {}
        analysis = extra.get("analysis")
        # 优先从有 structure 的章节中取
        if extra.get("structure") and not structure:
            structure = extra["structure"]
        # 构建章节条目
        entry = {
            "id": ch.id,
            "title": _sanitize_title(ch.title, ch.chapter_no),
            "chapter_no": ch.chapter_no,
            "word_count": ch.word_count,
            "volume": ch.volume,
            "analysis": analysis,
        }
        no = ch.chapter_no
        if no not in seen:
            seen[no] = entry
        else:
            # 新记录有分析结果时覆盖旧记录，确保保留已分析的数据
            if analysis and not seen[no].get("analysis"):
                seen[no] = entry

    # 按 chapter_no 升序排序
    chapter_list = sorted(seen.values(), key=lambda x: x["chapter_no"])

    return {
        "novel_id": novel_id,
        "novel_name": novel.name,
        "structure": structure,
        "chapters": chapter_list,
        "total": len(chapter_list),
    }
