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
from datetime import datetime, timezone

from sqlalchemy import select

from db import SessionLocal
from models.analysis_summary import StoryAnalysisSummary
from repositories import novel_repo, llm_repo
from services import task_service
from llm import langchain_factory as llm_adapters
from common import crypto, task_cancel
from common.exceptions import BizError
from config import TURBO_MAX_INPUT_CHARS

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


async def _chat(session, owner_id: int, messages: list[dict]) -> tuple[str, object]:
    """取默认对话模型适配器并调用 chat，返回 (文本, 用量)。"""
    cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
    if not cfgs:
        raise BizError(400, "尚未配置对话模型（llm_type=chat），请先在模型管理中添加")
    cfg = cfgs[0]
    adapter = llm_adapters.get_adapter(cfg, crypto.decrypt(cfg.api_key))
    text = await adapter.chat(messages)
    return text or "", adapter.get_last_usage()


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
                    [{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}])
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
                    [{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}])
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

            await task_service.record_llm_usage(
                owner_id, None, "turbo", f"turbo_{analysis_type}", tok_in, tok_out)

            if task_id:
                await task_service.update_task_progress(
                    task_id, stage="done", progress=100, status="success",
                    finished_at=datetime.now(timezone.utc),
                    tokens_in=tok_in, tokens_out=tok_out, summary_chars=len(summary))
            print(f"[turbo] {analysis_type} 完成 novel={novel_id} chunks={n} chars={len(summary)}")
        except Exception as e:
            if task_id:
                await task_service.update_task_progress(
                    task_id, stage="failed", status="failed",
                    error=str(e), finished_at=datetime.now(timezone.utc))
            print(f"[turbo] {analysis_type} 失败 novel={novel_id}: {e}")
