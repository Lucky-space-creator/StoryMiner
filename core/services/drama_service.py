"""
章节漫剧业务逻辑（M15 章节漫剧列）

整体思路：
    封装「章节漫剧」素材单元的 CRUD 与角色解析：用户选定某小说的连续章节范围
    [chapter_from, chapter_to]（最多 5 章），服务从范围内章节正文解析出场角色
    （基于已有人物档案匹配 + jieba 候选兜底），写入 story_chapter_drama。

关键点：
    1. 章节范围校验：chapter_to >= chapter_from，且跨度 (to - from + 1) <= 5。
    2. 出场角色解析：优先匹配该小说已有人物档案 name（精确 + 包含），再补 jieba 候选，
       最终回填 character_ids（story_character.id 列表）。
    3. owner 隔离：所有写操作前校验归属，越权抛 BizError。

实现逻辑：
    create 校验范围 → 取章节正文 → 解析角色 → 落库；list/get 委托 drama_repo。
"""
from common import nlp
from common.exceptions import BizError
from models.chapter_drama import ChapterDrama
from models.chapter_drama_scene import ChapterDramaScene
from models.character import Character
from repositories import drama_repo, novel_repo, character_repo, llm_repo
from llm import langchain_factory as llm_adapters
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _to_detail(d: ChapterDrama, characters: list[dict] | None = None) -> dict:
    """章节漫剧详情：含出场角色概要（若传入）。"""
    return {
        "id": d.id,
        "novel_id": d.novel_id,
        "title": d.title,
        "chapter_from": d.chapter_from,
        "chapter_to": d.chapter_to,
        "character_ids": d.character_ids or [],
        "characters": characters or [],
        "summary": d.summary,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


async def list_dramas(session: AsyncSession, novel_id: int) -> list[dict]:
    """章节漫剧列表（M15.1），附带每条的出场角色概要与已保存的导演场景标记。

    列表项补充 scene 字段（含 drama_id 与四段式概要），供前端判断「已生成漫剧场景」
    标记与详情直入；避免列表不携带 scene 导致保存后返回列表无法显示生成状态。
    """
    items = await drama_repo.list_by_novel(session, novel_id)
    result = []
    for d in items:
        chars = await _character_summaries(session, d.novel_id, d.character_ids or [])
        detail = _to_detail(d, chars)
        scene = await drama_repo.get_scene(session, d.id)
        detail["scene"] = _scene_to_detail(scene) if scene else None
        result.append(detail)
    return result


async def get_drama(session: AsyncSession, drama_id: int, owner_id: int) -> dict:
    """章节漫剧详情（M15.1），附带已保存的导演场景分析（若有）。"""
    d = await drama_repo.get(session, drama_id)
    if not d or d.owner_id != owner_id or d.deleted_at is not None:
        raise BizError(404, "章节漫剧不存在")
    chars = await _character_summaries(session, d.novel_id, d.character_ids or [])
    detail = _to_detail(d, chars)
    scene = await drama_repo.get_scene(session, drama_id)
    detail["scene"] = _scene_to_detail(scene) if scene else None
    return detail


async def _character_summaries(session: AsyncSession, novel_id: int, char_ids: list[int]) -> list[dict]:
    """按 character_ids 取人物概要（id/name/role）。"""
    if not char_ids:
        return []
    stmt = select(Character).where(
        Character.novel_id == novel_id,
        Character.id.in_(char_ids),
        Character.deleted_at.is_(None),
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [{"id": c.id, "name": c.name, "role": c.role} for c in rows]


async def create_drama(
    session: AsyncSession,
    novel_id: int,
    owner_id: int,
    payload: dict,
) -> dict:
    """新建章节漫剧（M15.2）：校验章节范围 → 解析出场角色 → 落库。

    payload: { title?, chapter_from, chapter_to, summary? }
    """
    chapter_from = payload.get("chapter_from")
    chapter_to = payload.get("chapter_to")
    if not isinstance(chapter_from, int) or not isinstance(chapter_to, int):
        raise BizError(400, "章节范围必须为整数")
    if chapter_to < chapter_from:
        raise BizError(400, "结束章节不能小于起始章节")
    span = chapter_to - chapter_from + 1
    if span > 5:
        raise BizError(400, f"章节范围不能超过 5 章（当前 {span} 章）")
    if span < 1:
        raise BizError(400, "章节范围无效")

    # 取该小说章节序号映射，校验范围存在且归属正确
    chapters = await novel_repo.list_all_chapters(session, novel_id)
    if not chapters:
        raise BizError(400, "该小说暂无章节")
    nos = {ch.chapter_no for ch in chapters}
    if chapter_from not in nos or chapter_to not in nos:
        raise BizError(400, "所选章节范围超出小说已有章节")

    # 拼接范围内章节正文
    full_text = "\n".join(
        ch.content for ch in chapters
        if chapter_from <= ch.chapter_no <= chapter_to and ch.content
    )

    # 解析出场角色：优先匹配已有人物档案名，再补 jieba 候选
    character_ids = await _extract_characters(session, novel_id, full_text)
    title = (payload.get("title") or f"第{chapter_from}-{chapter_to}章").strip()

    # 摘要自动生成：基于章节范围与出场角色，由系统统一生成（前端不填写）
    summary = _build_summary(chapter_from, chapter_to, character_ids)

    d = ChapterDrama(
        novel_id=novel_id, owner_id=owner_id, title=title,
        chapter_from=chapter_from, chapter_to=chapter_to,
        character_ids=character_ids,
        summary=summary,
    )
    await drama_repo.create(session, d)
    await session.commit()
    chars = await _character_summaries(session, novel_id, character_ids)
    return _to_detail(d, chars)


def _build_summary(chapter_from: int, chapter_to: int, character_ids: list[int]) -> str:
    """自动生成章节漫剧摘要（前端不填写）。

    优先展示出场角色数量与名单，并标注章节区间，供下游 AI 漫剧生产快速理解素材范围。
    角色名为占位说明，实际名单在落库后由 _character_summaries 补充（此处仅给数量提示）。
    """
    span = chapter_to - chapter_from + 1
    base = f"第 {chapter_from}–{chapter_to} 章（共 {span} 章）"
    if character_ids:
        return f"{base}，出场角色 {len(character_ids)} 名，详见角色列表。"
    return f"{base}，暂未匹配到已有人物档案中的出场角色。"


async def _extract_characters(session: AsyncSession, novel_id: int, full_text: str) -> list[int]:
    """解析出场角色：返回匹配的人物档案 id 列表。

    整体思路：
        「出场角色」= 章节正文中出现了该小说已建档人物档案的名字。由于匹配对象被
        限定为「已建档角色名」（都是真实存在的人物名，而非任意词），因此直接采用
        包含匹配即可，无需担心把普通词误判为角色。

    关键点：
        1. 中文人名两侧几乎总是中文字符（如「与苏晚在」），若强制「前后非中文」的
           整词边界，会导致绝大多数中文人名匹配失败——这是此前只命中部分角色的根因。
           故对已建档角色名改用直接包含匹配（name in full_text）。
        2. jieba 人物候选作为额外补充：仅当候选名能对应到已有档案且尚未命中时计入，
           覆盖「档案名为别名/正文用全名」等包含匹配漏掉的场景。

    实现逻辑：
        取档案名集合 → 逐个 name in full_text 命中 → jieba 候选补充 → 去重返回 id 列表。
    """
    existing = (await character_repo.list_by_novel(session, novel_id))
    name_to_id = {c.name: c.id for c in existing if c.name}
    if not full_text or not name_to_id:
        return []
    hit_ids: list[int] = []
    seen: set[int] = set()
    # 直接包含匹配：匹配对象为已建档角色名，出现即视为出场
    for name, cid in name_to_id.items():
        if name in full_text and cid not in seen:
            seen.add(cid)
            hit_ids.append(cid)
    # jieba 候选补充：仅当候选名能对应到已有档案且未命中时计入
    try:
        cands = nlp.extract_person_candidates(full_text, min_freq=1)
        for c in cands:
            nm = c.get("name")
            cid = name_to_id.get(nm)
            if cid is not None and cid not in seen:
                seen.add(cid)
                hit_ids.append(cid)
    except Exception:
        pass
    return hit_ids


# ---------------------------------------------------------------------------
# 导演 Agent（M15.5）：基于章节正文 + 出场角色生成四段式场景分析
# ---------------------------------------------------------------------------
_DIRECTOR_SYSTEM = (
    "你是一位资深的 AI 漫剧导演，擅长把小说章节改编为可拍摄的漫剧分镜脚本。对于每一部分内容，你必须使用 Markdown 风格的详细描述（如加粗、列表、分段），绝不允许粗略分点。\n"
    "给定【小说名】【章节范围】【章节正文】【出场角色】，请输出一个严格合法的 JSON 对象，仅包含以下四个字段：\n"
    "1. scene_design（场景设计）：字符串数组，每个元素为一个场景的完整视觉描述。必须写明：地点（具象化）、环境氛围（光线/色调/气味/天气）、关键视觉要素（标志性道具/色彩焦点）。"
    "允许在字符串内使用 Markdown（如 **加粗**、- 列表）来组织细节。\n"
    "2. plot_arrangement（剧情安排）：字符串数组，每个元素为一段连续情节节点，严格按时间顺序排列。"
    "需细腻刻画：人物动作（手势/步态）、台词（含语气）、神态（眼神/微表情）、情绪走向（如从压抑到爆发）及冲突推进。同样允许 Markdown 辅助层次化描述。\n"
    "3. camera_movement（镜头运转）：字符串数组，每个元素对应一条镜头语言。必须包含："
    "景别（远景/全景/中景/近景/特写/大特写）、运镜方式（推/拉/摇/移/跟/升/降/旋转）、转场建议（切/叠化/淡入淡出/匹配剪辑）以及构图侧重点（主体位置/视线引导）。可结合 Markdown 分项说明。\n"
    "4. duration_estimate（预计时长）：单个字符串，给出整个改编片段的预计总时长（如「约 2 分 30 秒」），并简要说明依据，例如镜头数量、平均时长、对话密度等。\n"
    "严格要求：只输出 JSON，不要任何解释文字。字段名必须完全一致：scene_design / plot_arrangement / camera_movement（均为字符串数组） / duration_estimate（字符串）。"
    "JSON 必须合法，所有字符串用双引号，数组元素用逗号分隔，禁止尾随逗号。描述必须具体、可落地，禁止空泛词汇。\n"
    "连载衔接要求：本片段是整部漫剧的一集。若章节范围不是开篇（非第 1 章起），"
    "请在 scene_design 首条点明「接续上文……」的转场场景，并在 plot_arrangement 中体现与上文的因果/悬念承接；"
    "各数组长度建议 3–6 条，与章节份量匹配，避免过多或过少。"
)

_EXPECT_KEYS = ("scene_design", "plot_arrangement", "camera_movement", "duration_estimate")


def _safe_list(v) -> list:
    """把模型返回的列表字段规整为字符串列表。"""
    if isinstance(v, list):
        return [str(x) for x in v if x not in (None, "")]
    return []


def _fallback_director(drama_title: str) -> dict:
    """LLM 不可用时的兜底结构化结果，保证前端仍可按四段式展示。"""
    return {
        "scene_design": [f"「{drama_title}」开场场景：选取章节核心冲突地点作为主舞台，突出环境氛围与人物关系。"],
        "plot_arrangement": ["按章节顺序提炼关键情节节点，设置起承转合的情绪曲线。"],
        "camera_movement": ["以中近景为主刻画人物微表情，关键冲突切换为手持跟拍增强临场感。"],
        "duration_estimate": "约 8–12 分钟（依据：所选章节正文规模与情节密度估算）",
        "content_raw": None,
    }


async def director_agent_generate(
    session: AsyncSession, owner_id: int, drama_id: int
) -> dict:
    """导演 Agent 生成场景分析（M15.5）：取章节正文 + 出场角色，调 LLM 产出四段式结构。

    返回 dict：{scene_design, plot_arrangement, camera_movement, duration_estimate, content_raw}。
    注意：本函数只生成不落库，保存由 save_drama_scene 负责。
    """
    d = await drama_repo.get(session, drama_id)
    if not d or d.owner_id != owner_id or d.deleted_at is not None:
        raise BizError(404, "章节漫剧不存在")
    chapters = await novel_repo.list_all_chapters(session, d.novel_id)
    full_text = "\n".join(
        ch.content for ch in chapters
        if d.chapter_from <= ch.chapter_no <= d.chapter_to and ch.content
    )
    chars = await _character_summaries(session, d.novel_id, d.character_ids or [])
    char_block = "、".join(f"{c['name']}（{c.get('role') or '角色'}）" for c in chars) or "（无已匹配角色）"

    user_prompt = (
        f"【小说名】{d.title}\n"
        f"【章节范围】第 {d.chapter_from}–{d.chapter_to} 章（共全文多章，本片段属{'开篇' if d.chapter_from == 1 else '后续/中段'}部分）\n"
        f"【出场角色】{char_block}\n"
        f"【章节正文】\n{full_text[:8000]}\n"
        "请基于以上内容生成漫剧分镜四段式分析 JSON，注意本片段与前后章节的衔接。"
    )

    try:
        cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
        if not cfgs:
            raise BizError(400, "尚未配置对话模型，请先在模型管理中添加 chat 类型模型")
        # 导演 Agent 优先选用本地 Ollama（provider=ollama）：响应快、免外网、结果稳定，
        # 避免优先选中远程大模型（如 gpt-5.4）时因网络/密钥导致生成接口长时间挂起。
        ollama_cfg = next((c for c in cfgs if (c.provider or "").lower() == "ollama"), None)
        cfg = ollama_cfg or cfgs[0]
        adapter = llm_adapters.get_adapter(cfg, None)
        messages = [
            {"role": "system", "content": _DIRECTOR_SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
        raw = await adapter.chat(messages, json_mode=True)
        parsed = _parse_director_json(raw)
        parsed["content_raw"] = raw
        return parsed
    except BizError:
        raise
    except Exception as e:  # LLM 调用失败时兜底，保证前端可用
        fb = _fallback_director(d.title)
        return fb


def _parse_director_json(raw: str) -> dict:
    """从模型输出中提取四段式 JSON；失败返回兜底。"""
    import json as _json
    text = (raw or "").strip()
    # 去除可能的代码块包裹
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        obj = _json.loads(text)
    except Exception:
        # 尝试截取首个 { 到末个 }
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e != -1:
            try:
                obj = _json.loads(text[s:e + 1])
            except Exception:
                return _fallback_director("当前漫剧")
        else:
            return _fallback_director("当前漫剧")
    return {
        "scene_design": _safe_list(obj.get("scene_design")),
        "plot_arrangement": _safe_list(obj.get("plot_arrangement")),
        "camera_movement": _safe_list(obj.get("camera_movement")),
        "duration_estimate": str(obj.get("duration_estimate") or ""),
        "content_raw": raw,
    }


async def save_drama_scene(
    session: AsyncSession, owner_id: int, drama_id: int, payload: dict
) -> dict:
    """保存导演 Agent 场景分析（M15.5）：upsert 到 story_chapter_drama_scene。"""
    d = await drama_repo.get(session, drama_id)
    if not d or d.owner_id != owner_id or d.deleted_at is not None:
        raise BizError(404, "章节漫剧不存在")
    scene = ChapterDramaScene(
        drama_id=d.id, owner_id=owner_id, novel_id=d.novel_id,
        scene_design=_safe_list(payload.get("scene_design")),
        plot_arrangement=_safe_list(payload.get("plot_arrangement")),
        camera_movement=_safe_list(payload.get("camera_movement")),
        duration_estimate=str(payload.get("duration_estimate") or "") or None,
        content_raw=payload.get("content_raw"),
    )
    saved = await drama_repo.upsert_scene(session, scene)
    await session.commit()
    return _scene_to_detail(saved)


def _scene_to_detail(s: ChapterDramaScene) -> dict:
    return {
        "drama_id": s.drama_id,
        "scene_design": s.scene_design or [],
        "plot_arrangement": s.plot_arrangement or [],
        "camera_movement": s.camera_movement or [],
        "duration_estimate": s.duration_estimate,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


async def get_drama_scene(session: AsyncSession, drama_id: int, owner_id: int) -> dict | None:
    """取已保存的场景分析；无则返回 None。"""
    d = await drama_repo.get(session, drama_id)
    if not d or d.owner_id != owner_id or d.deleted_at is not None:
        raise BizError(404, "章节漫剧不存在")
    s = await drama_repo.get_scene(session, drama_id)
    return _scene_to_detail(s) if s else None


async def delete_drama(session: AsyncSession, drama_id: int, owner_id: int) -> None:
    """删除章节漫剧（逻辑删除）。"""
    d = await drama_repo.get(session, drama_id)
    if not d or d.owner_id != owner_id:
        raise BizError(404, "章节漫剧不存在")
    await drama_repo.soft_delete(session, drama_id)
    await session.commit()
