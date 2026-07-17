"""
Skill 业务逻辑（M10 大模型 Skill 管理）

整体思路：
    聚合 Skill 的 CRUD、调试（渲染预览 + 实际 LLM 调用）、导入导出、内置库 seed 与挂载点加载，
    模板全程经 prompt_render 做 Jinja2 校验/渲染，保证变量契约一致。

关键点：
    1. 所有配置按 owner_id 隔离（读取含全局内置，删除仅限本人自建）。
    2. 内置库（builtin）首次启动 seed，可启停不可删；用户自建可改删。
    3. 调试（M10.5）：先校验语法→渲染预览→可选实际调 LLM 看输出。
    4. 挂载（M10.4）：collect_enabled 返回启用 Skill 渲染后的指令文本，供 M7/M8 注入 prompt。

实现逻辑：
    委托 skill_repo 数据访问、prompt_render 渲染、llm_adapters 实际调用；本层只做业务编排与字段映射。
"""
import json

from sqlalchemy.ext.asyncio import AsyncSession

from models.skill import Skill
from repositories import skill_repo
from services import prompt_render, llm_adapters
from common import crypto
from common.exceptions import BizError
from repositories import llm_repo


# 各挂载点可用变量契约（M10.3），调试面板据此提供样例填充提示
MOUNT_VARIABLES = {
    "dialogue": ["character_profile", "rag_context", "history", "novel_name", "user_input"],
    "continue_write": ["context", "prompt", "style", "length", "perspective"],
    "extract": ["text", "entity_list"],
    "global": ["novel_name", "user_input"],
}


def skill_out(s: Skill) -> dict:
    """Skill 出参。"""
    return {
        "id": s.id, "owner_id": s.owner_id, "name": s.name,
        "description": s.description, "prompt_template": s.prompt_template,
        "trigger": s.trigger, "mount_point": s.mount_point,
        "enabled": s.enabled, "builtin": s.builtin,
        "variables": prompt_render.extract_variables(s.prompt_template),
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


async def create_skill(session: AsyncSession, owner_id: int, data: dict) -> dict:
    """新建 Skill（M10.1）：模板先经 Jinja2 语法校验。"""
    prompt_render.validate(data["prompt_template"])
    s = Skill(
        owner_id=owner_id, name=data["name"], description=data.get("description"),
        prompt_template=data["prompt_template"], trigger=data.get("trigger"),
        mount_point=data.get("mount_point", "global"), enabled=data.get("enabled", True),
        builtin=False,
    )
    await skill_repo.create(session, s)
    await session.commit()
    await session.refresh(s)
    return skill_out(s)


async def list_skills(session: AsyncSession, owner_id: int) -> list[dict]:
    """Skill 列表（M10.1）。"""
    items = await skill_repo.list_visible(session, owner_id)
    return [skill_out(s) for s in items]


async def update_skill(session: AsyncSession, owner_id: int, skill_id: int, data: dict) -> dict:
    """更新 Skill（M10.1）：模板变更需校验语法。"""
    s = await skill_repo.get_visible(session, owner_id, skill_id)
    if not s:
        raise BizError(404, "Skill 不存在")
    if "prompt_template" in data and data["prompt_template"] is not None:
        prompt_render.validate(data["prompt_template"])
        s.prompt_template = data["prompt_template"]
    for f in ("name", "description", "trigger", "mount_point", "enabled"):
        if f in data and data[f] is not None:
            setattr(s, f, data[f])
    await session.commit()
    await session.refresh(s)
    return skill_out(s)


async def set_enabled(session: AsyncSession, owner_id: int, skill_id: int, enabled: bool) -> dict:
    """启停 Skill（M10.2/M10.4）。"""
    s = await skill_repo.get_visible(session, owner_id, skill_id)
    if not s:
        raise BizError(404, "Skill 不存在")
    s.enabled = enabled
    await session.commit()
    await session.refresh(s)
    return skill_out(s)


async def delete_skill(session: AsyncSession, owner_id: int, skill_id: int) -> None:
    """删除 Skill（M10.1，内置项拒绝）。"""
    s = await skill_repo.get_visible(session, owner_id, skill_id)
    if not s:
        raise BizError(404, "Skill 不存在")
    try:
        await skill_repo.delete(session, s)
    except PermissionError:
        raise BizError(400, "内置 Skill 不可删除，仅可启停")
    await session.commit()


async def debug_skill(session: AsyncSession, owner_id: int, skill_id: int, context: dict, run: bool) -> dict:
    """调试 Skill（M10.5）：渲染预览；run=True 时实际调 LLM 看输出。"""
    s = await skill_repo.get_visible(session, owner_id, skill_id)
    if not s:
        raise BizError(404, "Skill 不存在")
    rendered = prompt_render.render(s.prompt_template, context or {})
    result = {"rendered": rendered, "output": None}
    if run:
        cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
        if not cfgs:
            raise BizError(400, "尚未配置对话模型（llm_type=chat），请先在模型管理中添加")
        cfg = cfgs[0]
        adapter = llm_adapters.get_adapter(cfg, crypto.decrypt(cfg.api_key))
        result["output"] = await adapter.chat([{"role": "user", "content": rendered}])
    return result


async def collect_enabled(session: AsyncSession, owner_id: int, mount_point: str, context: dict) -> list[str]:
    """挂载点加载（M10.4）：返回启用 Skill 渲染后的附加指令列表。"""
    skills = await skill_repo.list_enabled_by_mount(session, owner_id, mount_point)
    out = []
    for s in skills:
        try:
            out.append(prompt_render.render(s.prompt_template, context or {}))
        except Exception:
            continue  # 单条渲染失败不影响其它 Skill
    return out


async def export_skills(session: AsyncSession, owner_id: int) -> dict:
    """导出 Skill 为 JSON（M10.6，仅本人自建）。"""
    items = await skill_repo.list_visible(session, owner_id)
    data = [skill_out(s) for s in items if not s.builtin]
    return {"filename": "skills.json", "json": json.dumps(data, ensure_ascii=False, indent=2)}


async def import_skills(session: AsyncSession, owner_id: int, payload: list[dict]) -> int:
    """导入 Skill（M10.6）：跳过内置项，批量插入本人自建。"""
    count = 0
    for item in payload:
        tmpl = item.get("prompt_template")
        if not tmpl or not item.get("name"):
            continue
        prompt_render.validate(tmpl)
        s = Skill(
            owner_id=owner_id, name=item["name"], description=item.get("description"),
            prompt_template=tmpl, trigger=item.get("trigger"),
            mount_point=item.get("mount_point", "global"),
            enabled=item.get("enabled", True), builtin=False,
        )
        await skill_repo.create(session, s)
        count += 1
    await session.commit()
    return count


# ---------------------------------------------------------------------------
# 内置 Skill 库（M10.2）：首次启动 seed，幂等（已存在则跳过）
# ---------------------------------------------------------------------------
_BUILTIN_SKILLS = [
    {
        "name": "关系抽取增强",
        "description": "在实体关系抽取时，强制输出结构化三元组并附证据句。",
        "prompt_template": "【关系抽取指令】请基于文本抽取人物关系三元组 (头,关系,尾,证据句)，"
                           "关系类型限定：师徒/恋人/亲属/仇敌/同门/主从。\n文本：{{ text }}",
        "trigger": "抽取关系时自动挂载",
        "mount_point": "extract",
    },
    {
        "name": "人物小传润色",
        "description": "生成人物小传时，按「身份-性格-外貌-口头禅」结构化输出。",
        "prompt_template": "【人物小传指令】请基于以下出场信息，生成结构化人物小传，"
                           "分「身份 / 性格 / 外貌 / 口头禅 / 简介」五节：\n人物：{{ character_profile }}",
        "trigger": "生成人物简介时自动挂载",
        "mount_point": "global",
    },
    {
        "name": "续写风格约束",
        "description": "续写时强制保持人设与世界观一致，避免 OOC。",
        "prompt_template": "【续写约束】请严格延续前文的人物设定与世界观：{{ context }}\n"
                           "创作提示：{{ prompt }}\n保持文风：{{ style }}，篇幅：{{ length }}，视角：{{ perspective }}。",
        "trigger": "续写时自动挂载",
        "mount_point": "continue_write",
    },
    {
        "name": "对话人设强化",
        "description": "对话时强化角色口吻与世界观一致性。",
        "prompt_template": "【角色对话约束】你正在扮演：{{ character_profile }}。\n"
                           "背景参考：{{ rag_context }}\n历史：{{ history }}\n"
                           "请以该角色口吻自然回应，不跳出人设。",
        "trigger": "对话时自动挂载",
        "mount_point": "dialogue",
    },
]


async def seed_builtin(session: AsyncSession) -> int:
    """首次启动 seed 内置 Skill 库（M10.2），幂等。"""
    existing = await skill_repo.list_visible(session, owner_id=0)
    existing_names = {s.name for s in existing if s.builtin}
    count = 0
    for item in _BUILTIN_SKILLS:
        if item["name"] in existing_names:
            continue
        s = Skill(
            owner_id=None, name=item["name"], description=item["description"],
            prompt_template=item["prompt_template"], trigger=item["trigger"],
            mount_point=item["mount_point"], enabled=True, builtin=True,
        )
        await skill_repo.create(session, s)
        count += 1
    if count:
        await session.commit()
    return count
