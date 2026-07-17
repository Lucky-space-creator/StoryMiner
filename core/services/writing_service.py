"""
续写与概览业务逻辑（M8 情节概览与续写）

整体思路：
    编排「概览/时间线/角色弧线（一次性 LLM 生成）+ 续写（WebSocket 流式）+ 版本持久化 + 入库采纳」全链路，
    复用 M1 的 Chapter 正文、M6 的人物卡、M9 的 chat/embed 适配器，续写流式复用 llm_adapters.chat_stream。

关键点：
    1. 概览/时间线/角色弧线：拼接章节正文（截断上限）或人物卡，调 adapter.chat 一次性生成，不落库。
    2. 续写流式：取前文（指定章节或最新章末尾片段）→ 构造含风格/长度/视角的 prompt → chat_stream 逐 token yield。
    3. 多版本：stream_continue 只负责产出，完整正文由调用方（WS）累计后调 save_version 持久化。
    4. 入库：adopt_as_chapter 将版本正文写为小说新章节（chapter_no 递增）。

实现逻辑：
    取 chat 适配器走 llm_repo.list_for_dispatch 默认配置；正文截断避免超长；
    消息与会话持久化由调用方 commit（REST 走 Depends，WS 走自建 session）。
"""
from sqlalchemy.ext.asyncio import AsyncSession

from models.novel_content import Novel, Chapter
from models.story_writing import ContinueWrite
from repositories import novel_repo, character_repo, writing_repo, llm_repo
from services import llm_adapters
from common import crypto
from common.exceptions import BizError


# 续写前文取末尾片段字数上限
CONTEXT_CHARS = 1500
# 概览/时间线拼接正文总字数上限
SUMMARY_CHARS = 8000

# 风格 / 长度 / 视角 中文描述映射（M8.5）
_STYLE_DESC = {
    "original": "贴合原作风格，保持既有叙事语气",
    "tense": "紧张悬疑，节奏紧凑，制造悬念",
    "warm": "温情舒缓，细腻柔和",
}
_LENGTH_DESC = {
    "short": "约200字",
    "mid": "约500字",
    "long": "约1000字",
}
_POV_DESC = {
    "third": "第三人称（全知或限知）视角",
    "first": "第一人称视角",
}


# ---------------------------------------------------------------------------
# 内部 helper
# ---------------------------------------------------------------------------
async def _get_chat_adapter(session: AsyncSession, owner_id: int):
    """取默认 chat 适配器，无配置抛 BizError。"""
    cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
    if not cfgs:
        raise BizError(400, "尚未配置对话模型（llm_type=chat），请先在模型管理中添加")
    cfg = cfgs[0]
    return llm_adapters.get_adapter(cfg, crypto.decrypt(cfg.api_key))


async def _load_novel(session: AsyncSession, owner_id: int, novel_id: int) -> Novel:
    """校验小说归属并返回实例。"""
    novel = await session.get(Novel, novel_id)
    if not novel or getattr(novel, "owner_id", None) != owner_id:
        raise BizError(404, "小说不存在或无权限")
    return novel


async def _collect_chapters_text(session: AsyncSession, novel_id: int, max_total: int) -> str:
    """拼接章节正文（按章号顺序），截断到 max_total 字。"""
    chapters: list[Chapter] = await novel_repo.list_chapters(session, novel_id)
    if not chapters:
        return ""
    parts, total = [], 0
    for c in chapters:
        seg = f"【{c.title or ('第'+str(c.chapter_no)+'章')}】\n{c.content}"
        if total + len(seg) > max_total:
            seg = seg[: max(0, max_total - total)]
            parts.append(seg)
            break
        parts.append(seg)
        total += len(seg)
    return "\n\n".join(parts)


async def _build_context(session: AsyncSession, novel_id: int, chapter_id: int | None) -> str:
    """取续写前文：指定章节取全文，否则取最新章节末尾片段。"""
    if chapter_id:
        ch = await novel_repo.get_chapter(session, chapter_id)
        if not ch:
            raise BizError(404, "指定章节不存在")
        text = ch.content
    else:
        chapters: list[Chapter] = await novel_repo.list_chapters(session, novel_id)
        if not chapters:
            raise BizError(400, "该小说暂无章节，无法续写")
        text = chapters[-1].content
    return text[-CONTEXT_CHARS:] if len(text) > CONTEXT_CHARS else text


# ---------------------------------------------------------------------------
# 一次性生成：概览 / 时间线 / 角色弧线（M8.1/M8.2/M8.3）
# ---------------------------------------------------------------------------
async def generate_summary(session: AsyncSession, owner_id: int, novel_id: int) -> str:
    """生成情节概览（M8.1）：按章/卷梳理故事线摘要。"""
    novel = await _load_novel(session, owner_id, novel_id)
    text = await _collect_chapters_text(session, novel_id, SUMMARY_CHARS)
    if not text:
        raise BizError(400, "该小说暂无正文，无法生成概览")
    adapter = await _get_chat_adapter(session, owner_id)
    messages = [{
        "role": "user",
        "content": (
            f"你是一位资深小说编辑。请阅读小说《{novel.name}》的章节内容，生成一份【情节概览】。\n"
            "要求：按章节/卷梳理故事线，提炼每条情节的核心事件与推进，语言简练、有层次。\n"
            f"=== 正文（节选）===\n{text}\n=== 结束 ===\n"
            "请直接输出概览，使用 Markdown 列表或分段，不要添加多余解释。"
        ),
    }]
    return await adapter.chat(messages)


async def generate_timeline(session: AsyncSession, owner_id: int, novel_id: int) -> str:
    """生成时间线梳理（M8.2）：提取事件时间轴并纠正乱序。"""
    novel = await _load_novel(session, owner_id, novel_id)
    text = await _collect_chapters_text(session, novel_id, SUMMARY_CHARS)
    if not text:
        raise BizError(400, "该小说暂无正文，无法生成时间线")
    adapter = await _get_chat_adapter(session, owner_id)
    messages = [{
        "role": "user",
        "content": (
            f"请基于小说《{novel.name}》正文，提取关键事件并梳理成【时间线】。\n"
            "要求：按事件发生的时间顺序排列；若原文存在时间乱序请纠正并标注；"
            "每条事件用一句话概括，可附章节出处。\n"
            f"=== 正文（节选）===\n{text}\n=== 结束 ===\n"
            "直接输出时间线（时间 → 事件 的列表形式），不要添加多余解释。"
        ),
    }]
    return await adapter.chat(messages)


async def generate_character_arc(session: AsyncSession, owner_id: int, novel_id: int) -> str:
    """生成角色弧线概览（M8.3）：主要人物成长/变化轨迹。"""
    novel = await _load_novel(session, owner_id, novel_id)
    chars = await character_repo.list_by_novel(session, novel_id)
    if not chars:
        raise BizError(400, "该小说暂无人物档案，无法生成角色弧线")
    chars_text = "\n\n".join(
        f"【{c.name}】"
        + (f" 身份：{c.identity}" if c.identity else "")
        + (f" 性格：{c.personality}" if c.personality else "")
        + (f" 简介：{c.description}" if c.description else "")
        for c in chars
    )
    adapter = await _get_chat_adapter(session, owner_id)
    messages = [{
        "role": "user",
        "content": (
            f"以下是小说《{novel.name}》的主要人物档案：\n{chars_text}\n\n"
            "请结合上述人物，生成【角色弧线概览】：概述每位主要人物的成长/变化轨迹"
            "（起点状态 → 关键转折 → 当前状态）。\n"
            "直接输出，按人物分小节，不要添加多余解释。"
        ),
    }]
    return await adapter.chat(messages)


# ---------------------------------------------------------------------------
# 续写流式（M8.4/M8.5）+ 版本持久化（M8.6）
# ---------------------------------------------------------------------------
async def stream_continue(
    session: AsyncSession, owner_id: int, novel_id: int, payload: dict
):
    """续写流式生成器（M8.4/M8.5）：逐 token yield，结束后由调用方累计并 save_version。"""
    await _load_novel(session, owner_id, novel_id)
    style = payload.get("style", "original")
    length = payload.get("length", "mid")
    pov = payload.get("perspective", "third")
    prompt = (payload.get("prompt") or "").strip()
    chapter_id = payload.get("chapter_id")

    context = await _build_context(session, novel_id, chapter_id)
    adapter = await _get_chat_adapter(session, owner_id)

    system = (
        "你是一位小说续写助手。请根据【前文】与【创作要求】继续创作后续情节。\n"
        f"【前文】（末尾片段）：\n{context}\n"
        f"【用户提示】：{prompt or '（无，请自然延续）'}\n"
        "【创作要求】：\n"
        f"- 文风：{_STYLE_DESC.get(style, _STYLE_DESC['original'])}\n"
        f"- 篇幅：{_LENGTH_DESC.get(length, _LENGTH_DESC['mid'])}\n"
        f"- 视角：{_POV_DESC.get(pov, _POV_DESC['third'])}\n"
        "- 严格延续前文的人物、世界观与叙事节奏；不要重复前文结尾，自然衔接展开。\n"
        "直接输出续写正文，不要加任何解释、标题或前缀。"
    )
    # 注入启用的续写 Skill 附加指令（M10.4）：挂载点 continue_write 或 global 的启用项
    try:
        from services import skill_service
        hints = await skill_service.collect_enabled(session, owner_id, "continue_write", {
            "context": context, "prompt": prompt, "style": style, "length": length, "perspective": pov,
        })
        if hints:
            system += "\n\n=== 附加指令（Skill）===\n" + "\n".join(hints)
    except Exception:
        pass
    messages = [{"role": "user", "content": system}]
    async for piece in adapter.chat_stream(messages):
        yield piece


async def save_version(
    session: AsyncSession, owner_id: int, novel_id: int, payload: dict, content: str
) -> ContinueWrite:
    """持久化一条续写版本（M8.6）。"""
    version = ContinueWrite(
        owner_id=owner_id,
        novel_id=novel_id,
        chapter_id=payload.get("chapter_id"),
        title=payload.get("title"),
        style=payload.get("style", "original"),
        length=payload.get("length", "mid"),
        perspective=payload.get("perspective", "third"),
        prompt=payload.get("prompt"),
        content=content,
        word_count=len(content.strip()),
    )
    return await writing_repo.save_version(session, version)


async def list_versions(session: AsyncSession, owner_id: int, novel_id: int) -> list[dict]:
    """续写版本列表（M8.6）。"""
    items = await writing_repo.list_versions(session, owner_id, novel_id)
    return [{
        "id": v.id, "novel_id": v.novel_id, "chapter_id": v.chapter_id,
        "title": v.title, "style": v.style, "length": v.length,
        "perspective": v.perspective, "prompt": v.prompt,
        "word_count": v.word_count,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "preview": v.content[:120],
    } for v in items]


async def adopt_as_chapter(session: AsyncSession, owner_id: int, version_id: int) -> dict:
    """采纳续写为新章节（M8.7）：写入 story_chapter，chapter_no 递增。"""
    version = await writing_repo.get_version(session, owner_id, version_id)
    if not version:
        raise BizError(404, "续写版本不存在或无权限")
    chapters: list[Chapter] = await novel_repo.list_chapters(session, version.novel_id)
    next_no = (max((c.chapter_no for c in chapters), default=0) + 1) if chapters else 1
    style_label = {"original": "原风格", "tense": "悬疑", "warm": "温情"}.get(version.style, version.style)
    new_chapter = Chapter(
        novel_id=version.novel_id,
        owner_id=owner_id,
        title=version.title or f"续写·{style_label}",
        chapter_no=next_no,
        content=version.content,
        word_count=len(version.content.strip()),
    )
    await novel_repo.add_chapters(session, [new_chapter])
    await session.flush()
    return {
        "chapter_id": new_chapter.id,
        "novel_id": version.novel_id,
        "chapter_no": next_no,
        "title": new_chapter.title,
    }
