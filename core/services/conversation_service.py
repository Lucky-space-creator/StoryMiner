"""
对话业务逻辑（M7 用户 × 小说人物对话）

整体思路：
    编排「建会话→构造角色扮演 system prompt→RAG 检索背景→多轮历史→流式对话」全链路，
    复用 M3/M4 的 chunk 向量库做 RAG，复用 M9 的 chat/embed 适配器做生成与嵌入，
    通过 WebSocket 逐 token 回传（见 routers/ws_chat.py）。

关键点：
    1. RAG 上下文：按 novel 取首个 KB，embed 用户问题，调 chunk_repo.similar 取 top_k 片段。
    2. 角色扮演 system：单角色聚焦人设，多角色（群聊）并列人设并提示轮流发言。
    3. 多轮：从 story_conversation_message 取历史，拼在 system 之后、当前问题之前。
    4. 流式：stream_reply 为异步生成器，逐 token yield，结束持久化 user/assistant 消息。

实现逻辑：
    取 embed/chat 适配器走 llm_repo.list_for_dispatch 默认配置；向量库集合 kb_{kb_id}；
    消息与会话持久化由调用方 commit（REST 走 Depends，WS 走自建 session）。
"""
import json

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models.conversation import Conversation, ConversationMessage
from models.novel_content import Novel
from models.character import Character
from repositories import character_repo, kb_repo, chunk_repo, llm_repo
from services import llm_adapters
from common import crypto
from common.exceptions import BizError


# ---------------------------------------------------------------------------
# 视图转换
# ---------------------------------------------------------------------------
def _chars_view(chars: list[Character]) -> list[dict]:
    """人物简略视图（列表/详情复用）。"""
    return [{"id": c.id, "name": c.name, "role": c.role} for c in chars]


def _conv_view(conv: Conversation, chars: list[Character], novel_name: str) -> dict:
    """会话视图：补人物简略信息与小说名。"""
    return {
        "id": conv.id,
        "title": conv.title,
        "novel_id": conv.novel_id,
        "novel_name": novel_name,
        "character_ids": conv.character_ids,
        "characters": _chars_view(chars),
        "favorite": conv.favorite,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
    }


# ---------------------------------------------------------------------------
# 会话管理（REST）
# ---------------------------------------------------------------------------
async def create_conversation(
    session: AsyncSession, owner_id: int, novel_id: int, character_ids: list[int], title: str | None
) -> dict:
    """新建会话（M7.1）：校验小说与人物归属，存 character_ids 与标题。"""
    novel = await session.get(Novel, novel_id)
    if not novel or getattr(novel, "owner_id", None) != owner_id:
        raise BizError(404, "小说不存在或无权限")
    if not character_ids:
        raise BizError(400, "请至少选择一个人物")
    # 校验人物均属于该小说且未删除
    chars: list[Character] = []
    for cid in character_ids:
        c = await character_repo.get(session, cid)
        if not c or c.novel_id != novel_id or c.deleted_at is not None:
            raise BizError(400, f"人物不存在或不属于该小说：id={cid}")
        chars.append(c)
    conv = Conversation(
        owner_id=owner_id,
        novel_id=novel_id,
        title=title or f"与{chars[0].name}的对话",
        character_ids=list(character_ids),
    )
    await conversation_repo_create(session, conv)
    return _conv_view(conv, chars, novel.name)


async def list_conversations(session: AsyncSession, owner_id: int) -> list[dict]:
    """会话列表（M7.6）：返回当前用户全部会话及人物/小说概览。"""
    convs, _ = await conversation_repo_list(session, owner_id)
    out = []
    for conv in convs:
        chars = await _load_chars(session, conv.character_ids)
        novel = await session.get(Novel, conv.novel_id)
        out.append(_conv_view(conv, chars, novel.name if novel else ""))
    return out


async def get_conversation(session: AsyncSession, owner_id: int, conv_id: int) -> dict:
    """会话详情（M7.6）：含多轮消息历史与人物档案。"""
    conv = await conversation_repo_get(session, owner_id, conv_id)
    if not conv:
        raise BizError(404, "会话不存在")
    chars = await _load_chars(session, conv.character_ids)
    novel = await session.get(Novel, conv.novel_id)
    msgs = await conversation_repo_messages(session, conv_id)
    return {
        **_conv_view(conv, chars, novel.name if novel else ""),
        "messages": [
            {"role": m.role, "content": m.content, "character_id": m.character_id}
            for m in msgs
        ],
    }


async def delete_conversation(session: AsyncSession, owner_id: int, conv_id: int) -> None:
    """删除会话（M7.6）：级联清消息。"""
    conv = await conversation_repo_get(session, owner_id, conv_id)
    if not conv:
        raise BizError(404, "会话不存在")
    await conversation_repo_delete(session, conv_id)


async def toggle_favorite(session: AsyncSession, owner_id: int, conv_id: int) -> None:
    """收藏/取消收藏（M7.6）。"""
    conv = await conversation_repo_get(session, owner_id, conv_id)
    if not conv:
        raise BizError(404, "会话不存在")
    await conversation_repo_fav(session, owner_id, conv_id, not conv.favorite)


async def export_markdown(session: AsyncSession, owner_id: int, conv_id: int) -> dict:
    """导出对话为 Markdown（M7.8）。"""
    data = await get_conversation(session, owner_id, conv_id)
    lines = [f"# {data.get('title') or '对话记录'}", "", f"> 小说：《{data.get('novel_name')}》", ""]
    for m in data["messages"]:
        speaker = "我" if m["role"] == "user" else (data["characters"][0]["name"] if data["characters"] else "角色")
        lines.append(f"**{speaker}**：{m['content']}")
        lines.append("")
    return {"filename": f"{data.get('title') or 'conversation'}.md", "markdown": "\n".join(lines)}


# ---------------------------------------------------------------------------
# 流式对话核心（WebSocket 调用）
# ---------------------------------------------------------------------------
async def _build_system_prompt(session, owner_id, novel_name: str, chars: list[Character], rag_context: str) -> str:
    """构造角色扮演 system prompt（M7.2/M7.3/M7.7），并注入启用的对话 Skill 附加指令（M10.4）。"""
    if len(chars) == 1:
        c = chars[0]
        p = f"你正在扮演小说《{novel_name}》中的角色「{c.name}」。\n角色设定：\n"
        if c.gender:
            p += f"- 性别：{c.gender}\n"
        if c.identity:
            p += f"- 身份：{c.identity}\n"
        if c.personality:
            p += f"- 性格：{c.personality}\n"
        if c.appearance:
            p += f"- 外貌：{c.appearance}\n"
        if c.catchphrase:
            p += f"- 口头禅：{c.catchphrase}\n"
        if c.description:
            p += f"- 简介：{c.description}\n"
        p += "\n请严格以该角色的口吻、性格、世界观与语言风格回应，不要跳出角色，不要声明自己是 AI。"
    else:
        names = "、".join(c.name for c in chars)
        p = f"你正在参与小说《{novel_name}》中多人同场的对话，在场角色有：{names}。\n"
        for c in chars:
            p += f"\n【{c.name}】"
            if c.identity:
                p += f"身份{c.identity}；"
            if c.personality:
                p += f"性格{c.personality}；"
            if c.description:
                p += f"简介{c.description}；"
        p += "\n请让每位角色按其性格与设定轮流自然发言，并以「角色名：」作为发言前缀。"

    if rag_context:
        p += f"\n\n=== 相关剧情背景（RAG 检索，仅作背景参考，勿直接复述原文）===\n{rag_context}\n"

    # 注入启用的对话 Skill 附加指令（M10.4）：挂载点 dialogue 或 global 的启用项
    try:
        from services import skill_service
        hints = await skill_service.collect_enabled(session, owner_id, "dialogue", {
            "character_profile": (chars[0].description or "") if chars else "",
            "rag_context": rag_context, "history": "", "novel_name": novel_name, "user_input": "",
        })
        if hints:
            p += "\n\n=== 附加指令（Skill）===\n" + "\n".join(hints)
    except Exception:
        pass
    return p


async def _embed_query(session: AsyncSession, owner_id: int, query: str) -> list[float] | None:
    """取默认 embed 适配器并嵌入查询句，失败返回 None（RAG 降级为无背景）。"""
    cfgs = await llm_repo.list_for_dispatch(session, owner_id, "embed")
    if not cfgs:
        return None
    cfg = cfgs[0]
    adapter = llm_adapters.get_adapter(cfg, crypto.decrypt(cfg.api_key))
    try:
        return (await adapter.embed([query]))[0]
    except Exception:
        return None


async def _rag_context(session: AsyncSession, owner_id: int, novel_id: int, query: str) -> str:
    """RAG 检索背景（M7.3）：按 novel 取 KB → embed → similar → 拼文本。"""
    kbs, _ = await kb_repo.list_kbs(session, owner_id, novel_id, 1, 1)
    if not kbs:
        return ""
    vector = await _embed_query(session, owner_id, query)
    if not vector:
        return ""
    hits = await chunk_repo.similar(session, kbs[0].id, vector, len(vector), top_k=4)
    if not hits:
        return ""
    return "\n\n".join(f"[参考片段]\n{h['content']}" for h in hits)


async def stream_reply(
    session: AsyncSession, owner_id: int, conv_id: int, user_content: str
):
    """流式回复生成器（M7.4/M7.5）：逐 token yield，结束后持久化消息。"""
    conv = await conversation_repo_get(session, owner_id, conv_id)
    if not conv:
        raise BizError(404, "会话不存在")
    chars = await _load_chars(session, conv.character_ids)
    if not chars:
        raise BizError(400, "会话未关联有效人物")

    novel = await session.get(Novel, conv.novel_id)
    novel_name = novel.name if novel else ""

    # RAG 背景 + 角色扮演 system（首次会话快照 system_prompt）
    rag = await _rag_context(session, owner_id, conv.novel_id, user_content)
    system_prompt = await _build_system_prompt(session, owner_id, novel_name, chars, rag)
    if not conv.system_prompt:
        conv.system_prompt = system_prompt

    # 多轮历史
    history = await conversation_repo_messages(session, conv_id)
    messages = [{"role": "system", "content": system_prompt}]
    messages += [{"role": m.role, "content": m.content} for m in history]
    messages.append({"role": "user", "content": user_content})

    # 持久化 user 消息
    await conversation_repo_add(session, ConversationMessage(
        conv_id=conv_id, role="user", content=user_content,
    ))

    # 取 chat 适配器
    cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
    if not cfgs:
        raise BizError(400, "尚未配置对话模型（llm_type=chat），请先在模型管理中添加")
    cfg = cfgs[0]
    adapter = llm_adapters.get_adapter(cfg, crypto.decrypt(cfg.api_key))

    # 流式产出并累积
    full = ""
    async for piece in adapter.chat_stream(messages):
        full += piece
        yield piece

    # 持久化 assistant 消息（群聊标记首个角色）
    char_id = chars[0].id if len(chars) == 1 else None
    await conversation_repo_add(session, ConversationMessage(
        conv_id=conv_id, role="assistant", content=full, character_id=char_id,
        tokens_out=len(full),
    ))


# ---------------------------------------------------------------------------
# 内部 helper（避免与 repositories 包循环导入，统一在此调用 repo）
# ---------------------------------------------------------------------------
from repositories import conversation_repo as _repo


async def _load_chars(session: AsyncSession, char_ids: list) -> list[Character]:
    """按 id 列表加载未删除人物，保持传入顺序。"""
    chars: list[Character] = []
    for cid in (char_ids or []):
        c = await character_repo.get(session, cid)
        if c and c.deleted_at is None:
            chars.append(c)
    return chars


async def conversation_repo_create(session, conv):
    return await _repo.create_conv(session, conv)


async def conversation_repo_list(session, owner_id):
    return await _repo.list_convs(session, owner_id)


async def conversation_repo_get(session, owner_id, conv_id):
    return await _repo.get_conv(session, owner_id, conv_id)


async def conversation_repo_delete(session, conv_id):
    return await _repo.delete_conv(session, conv_id)


async def conversation_repo_add(session, msg):
    return await _repo.add_message(session, msg)


async def conversation_repo_messages(session, conv_id):
    return await _repo.list_messages(session, conv_id)


async def conversation_repo_fav(session, owner_id, conv_id, favorite):
    return await _repo.set_favorite(session, owner_id, conv_id, favorite)
