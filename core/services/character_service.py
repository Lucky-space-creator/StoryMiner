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

from sqlalchemy.ext.asyncio import AsyncSession

from models.character import Character
from repositories import character_repo, novel_repo, llm_repo
from services import llm_adapters
from common import crypto
from common.exceptions import BizError

# 前端展示字段映射：列表项仅需 name/role/desc/appearances
_LIST_FIELDS = ("id", "name", "role", "description", "appearances")


def _to_list_item(c: Character) -> dict:
    """列表项：映射 description→desc（前端字段名）。"""
    return {
        "id": c.id, "name": c.name, "role": c.role,
        "desc": c.description, "appearances": c.appearances,
    }


def _to_detail(c: Character) -> dict:
    """详情：返回完整档案。"""
    return {
        "id": c.id, "name": c.name, "role": c.role, "gender": c.gender,
        "identity": c.identity, "personality": c.personality,
        "appearance": c.appearance, "catchphrase": c.catchphrase,
        "desc": c.description, "avatar": c.avatar, "source": c.source,
        "appearances": c.appearances,
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
    """删除人物（软删除）。"""
    c = await character_repo.get(session, char_id)
    if not c or c.owner_id != owner_id or c.deleted_at is not None:
        raise BizError(404, "人物不存在")
    await character_repo.soft_delete(session, char_id)
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


async def generate_profile(session: AsyncSession, char_id: int, owner_id: int) -> dict:
    """AI 生成小传（M6.2）：基于全文生成结构化档案并写回。"""
    c = await character_repo.get(session, char_id)
    if not c or c.owner_id != owner_id or c.deleted_at is not None:
        raise BizError(404, "人物不存在")
    chapters = await novel_repo.list_chapters(session, c.novel_id)
    if not chapters:
        raise BizError(400, "该小说暂无章节，无法生成小传")
    text = "\n".join(ch.content for ch in chapters)[:12000]
    adapter, _ = await _get_chat_adapter(session, owner_id)
    raw = await adapter.chat([{"role": "user", "content": _GEN_PROMPT.replace("{name}", c.name).replace("{text}", text)}])
    data = _parse_json(raw)

    if data:
        c.role = data.get("role") or c.role
        c.gender = data.get("gender") or c.gender
        c.identity = data.get("identity") or c.identity
        c.personality = data.get("personality") or c.personality
        c.appearance = data.get("appearance") or c.appearance
        c.catchphrase = data.get("catchphrase") or c.catchphrase
        c.description = data.get("description") or c.description
        c.source = "auto"
        # 统计出场次数：人物名在全文出现次数
        c.appearances = text.count(c.name)
        await session.commit()
    return _to_detail(c)


def _parse_json(raw: str) -> dict:
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
