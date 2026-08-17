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
from llm import langchain_factory as llm_adapters
from common import crypto
from common.exceptions import BizError
from prompts import (
    build_summary_prompt, build_timeline_prompt, build_character_arc_prompt, build_continue_system,
)


# 续写前文取末尾片段字数上限
CONTEXT_CHARS = 1500
# 概览/时间线拼接正文总字数上限
SUMMARY_CHARS = 8000


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
    chapters: list[Chapter] = await novel_repo.list_all_chapters(session, novel_id)
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
        chapters: list[Chapter] = await novel_repo.list_all_chapters(session, novel_id)
        if not chapters:
            raise BizError(400, "该小说暂无章节，无法续写")
        text = chapters[-1].content
    return text[-CONTEXT_CHARS:] if len(text) > CONTEXT_CHARS else text


# ---------------------------------------------------------------------------
# 一次性生成：概览 / 时间线 / 角色弧线（M8.1/M8.2/M8.3）
# ---------------------------------------------------------------------------
async def generate_summary(session: AsyncSession, owner_id: int, novel_id: int, detail: str = "brief") -> str:
    """生成情节概览（M8.1）：按章/卷梳理故事线摘要，结果按 (kind,detail) 覆盖落库缓存。"""
    novel = await _load_novel(session, owner_id, novel_id)
    text = await _collect_chapters_text(session, novel_id, SUMMARY_CHARS)
    if not text:
        raise BizError(400, "该小说暂无正文，无法生成概览")
    adapter = await _get_chat_adapter(session, owner_id)
    messages = [{"role": "user", "content": build_summary_prompt(novel.name, text, detail)}]
    result = await adapter.chat(messages)
    await writing_repo.save_analysis(session, owner_id, novel_id, "summary", detail, result)
    return result


async def generate_timeline(session: AsyncSession, owner_id: int, novel_id: int, detail: str = "brief") -> str:
    """生成时间线梳理（M8.2）：提取事件时间轴并纠正乱序，结果落库缓存。"""
    novel = await _load_novel(session, owner_id, novel_id)
    text = await _collect_chapters_text(session, novel_id, SUMMARY_CHARS)
    if not text:
        raise BizError(400, "该小说暂无正文，无法生成时间线")
    adapter = await _get_chat_adapter(session, owner_id)
    messages = [{"role": "user", "content": build_timeline_prompt(novel.name, text, detail)}]
    result = await adapter.chat(messages)
    await writing_repo.save_analysis(session, owner_id, novel_id, "timeline", detail, result)
    return result


async def generate_character_arc(session: AsyncSession, owner_id: int, novel_id: int, detail: str = "brief") -> str:
    """生成角色弧线概览（M8.3）：主要人物成长/变化轨迹，结果落库缓存。"""
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
    messages = [{"role": "user", "content": build_character_arc_prompt(novel.name, chars_text, detail)}]
    result = await adapter.chat(messages)
    await writing_repo.save_analysis(session, owner_id, novel_id, "character_arc", detail, result)
    return result


async def get_cached_analysis(
    session: AsyncSession, owner_id: int, novel_id: int, kind: str, detail: str = "brief"
) -> dict | None:
    """读取已缓存的概览类分析结果（M8 缓存复用）；无则返回 None，由前端触发生成。"""
    row = await writing_repo.get_analysis(session, owner_id, novel_id, kind, detail)
    if not row:
        return None
    return {"kind": kind, "detail": detail, "content": row.content, "word_count": row.word_count}


async def get_cached_analyses(session: AsyncSession, owner_id: int, novel_id: int) -> list[dict]:
    """批量读取某小说全部已缓存概览（前端初始化直接展示，避免重复生成）。"""
    rows = await writing_repo.get_analyses_by_novel(session, owner_id, novel_id)
    return [
        {"kind": r.kind, "detail": r.detail, "content": r.content, "word_count": r.word_count}
        for r in rows
    ]


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
    use_chars = bool(payload.get("use_chars", False))

    context = await _build_context(session, novel_id, chapter_id)
    adapter = await _get_chat_adapter(session, owner_id)

    # 结合人物设定：注入该小说人物档案，使续写更贴合角色性格与关系
    char_block = ""
    if use_chars:
        try:
            chars = await character_repo.list_by_novel(session, novel_id)
            if chars:
                char_block = "\n\n【人物设定参考】（请严格贴合以下角色性格/关系创作）：\n" + "\n".join(
                    f"- {c.name}"
                    + (f"（{c.identity}）" if c.identity else "")
                    + (f"：{c.personality}" if c.personality else "")
                    for c in chars
                )
        except Exception:
            char_block = ""

    system = build_continue_system(context, prompt, style, length, pov, char_block)
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
    chapters: list[Chapter] = await novel_repo.list_all_chapters(session, version.novel_id)
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


# ─────────────────────────────────────────────────────────────
# 用户编辑保存（M8.9）：用户在前端修改续写内容后，保存到 MinIO 新文件夹
# 对象键前缀固定为 continue_write/{owner_id}/{novel_id}/，实现「新建一个文件夹存放续写内容」
# ─────────────────────────────────────────────────────────────
def _continue_write_key(owner_id: int, novel_id: int, name: str) -> str:
    """生成续写保存对象键：continue_write/{owner}/{novel}/{name}.txt。"""
    safe = "".join(c for c in name if c.isalnum() or c in "-_")
    return f"continue_write/{owner_id}/{novel_id}/{safe or 'untitled'}.txt"


async def save_continue_write(
    session: AsyncSession,
    owner_id: int,
    novel_id: int,
    content: str,
    name: str | None = None,
) -> dict:
    """保存用户编辑后的续写内容到 MinIO（新建 continue_write 目录）。

    整体思路：纯存储动作，不改动数据库版本；用户可多次保存不同 name 形成多份草稿。
    关键点：name 经安全化防目录穿越；内容以 UTF-8 写入 MinIO，返回可回读的对象键。
    """
    from storage import minio_storage

    name = name or f"续写_{int(__import__('time').time())}"
    key = _continue_write_key(owner_id, novel_id, name)
    data = content.encode("utf-8")
    digest = minio_storage.sha256(data)
    # 直接以规范键 put 到 MinIO（continue_write 前缀目录），保证路径稳定、可回列可读
    await minio_storage.ensure_storage()
    import io as _io
    minio_storage._client.put_object(
        minio_storage.MINIO_BUCKET, key, _io.BytesIO(data), length=len(data)
    )
    return {
        "object_key": key,
        "name": name,
        "length": len(data),
        "digest": digest,
    }


async def list_continue_writes(session: AsyncSession, owner_id: int, novel_id: int) -> list[dict]:
    """列出某小说下用户保存的续写草稿（从 MinIO 按前缀列举）。"""
    from minio import Minio
    from config import MINIO_BUCKET
    from storage import minio_storage

    prefix = f"continue_write/{owner_id}/{novel_id}/"
    client: Minio = minio_storage._client
    items = []
    try:
        for obj in client.list_objects(MINIO_BUCKET, prefix=prefix, recursive=True):
            if not obj.object_name.endswith(".txt"):
                continue
            nm = obj.object_name.rsplit("/", 1)[-1][:-4]
            items.append({
                "name": nm,
                "object_key": obj.object_name,
                "size": obj.size,
                "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
            })
    except Exception:
        pass
    return items


async def read_continue_write(session: AsyncSession, owner_id: int, object_key: str) -> str:
    """回读某份续写草稿内容。"""
    from storage import minio_storage

    if not object_key.startswith(f"continue_write/{owner_id}/"):
        raise BizError(403, "无权访问该续写文件")
    data = await minio_storage.read(object_key)
    return data.decode("utf-8", errors="replace")
