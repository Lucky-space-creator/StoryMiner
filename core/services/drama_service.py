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
import asyncio
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
    """章节漫剧详情：含出场角色概要（若传入）与生成中状态标记。"""
    return {
        "id": d.id,
        "novel_id": d.novel_id,
        "title": d.title,
        "chapter_from": d.chapter_from,
        "chapter_to": d.chapter_to,
        "character_ids": d.character_ids or [],
        "characters": characters or [],
        "summary": d.summary,
        "generating": bool((d.extra or {}).get("generating", False)),
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
    # 需求2：漫剧片段按「单章」制作，一次只做一章（chapter_from == chapter_to）
    if chapter_to != chapter_from:
        raise BizError(400, "漫剧片段需逐章制作，章节范围必须为单章（chapter_from == chapter_to）")
    chapter_to = chapter_from  # 归一化，统一按单章处理

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

    单章片段：标注「第 X 章」与出场角色数量，供下游 AI 漫剧生产快速理解素材范围。
    """
    base = f"第 {chapter_from} 章（单章漫剧片段）"
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
class DirectorPrompt:
    """导演 Agent（M15.5）提示词集中管理：系统提示、用户提示、LLM 不可用兜底。

    整体思路：
        把原本零散在 _DIRECTOR_SYSTEM / _fallback_director / user_prompt 三处的提示词
        收敛到一个类内，统一维护四段式（场景设计 / 剧情安排 / 镜头运转 / 预计时长）约束，
        避免散落多处导致口径不一致。

    关键点：
        1. system_prompt 固化四段式结构与硬性约束（JSON 合法、数组 3–6 条、连载衔接）。
        2. build_user_prompt 按 drama 实体拼装【小说名/章节范围/出场角色/章节正文】。
        3. fallback 在 LLM 调用失败时返回结构化四段式，保证前端可展示。
    """

    # 系统提示：定义角色、四段式字段约束与输出格式
    # 关键调整（M15.5 增强）：明确要求「字数充足、细节丰富」，取消对弱模型的迁就——
    # 原先为兼容本地 7B 而限制数组条目数，现改为按章节份量充分展开，单条目也要求长描述。
    SYSTEM = (
        "你是一位资深的 AI 漫剧导演，擅长把小说章节改编为可拍摄的漫剧分镜脚本。你必须输出【内容丰富、细节充足、字数充分】的分镜，"
        "严禁粗略分点或字数过少；每一部分内容都必须使用 Markdown 风格的详细描述（如加粗、列表、分段）来展开。\n"
        "给定【小说名】【章节范围】【章节正文】【出场角色（含本章出场次数）】【上一章正文（供衔接）】，请输出一个严格合法的 JSON 对象，包含以下五个字段：\n"
        "0. prev_chapter_review（上集回顾）：单个字符串，150–300 字，概括上一章的大致内容与剧情发展，"
        "点明关键人物、核心冲突与未解悬念，让本集观众无需回顾即可衔接；并描写必要的环境与情绪铺垫。仅当给定了【上一章正文】时才输出本字段；若本集为开篇（无上一章）则输出空字符串 \"\"。\n"
        "1. scene_design（场景设计）：字符串数组，每个元素为一个场景的完整视觉描述，单条不少于 80 字。必须写明：地点（具象化到可想象）、环境氛围（光线/色调/气味/天气/温度）、关键视觉要素（标志性道具/色彩焦点/构图层次）、以及场景随情节推进的氛围变化。允许在字符串内使用 Markdown（如 **加粗**、- 列表）来组织细节。\n"
        "2. plot_arrangement（剧情安排）：字符串数组，每条都是一段【连续、详尽、像小说正文般可读】的漫剧动画剧情，单条不少于 当前章节的 80%，"
        "严格按时间顺序排列，每条必须同时、显式包含以下三要素，缺一不可：\n"
        "   (a) 人物神态与对话：必须写明「谁 + 何种神态/微表情/语气（如眉峰一蹙、指节发白、声音发颤）」，"
        "并给出贴合人设的【具体台词】——台词用中文双引号括起，要含潜台词与情绪，禁止只用「二人交谈」之类空写；\n"
        "   (b) 人物之间的交互：必须有双向动作反应（如一方逼近、另一方后退/握拳/冷笑），体现关系张力与冲突推进，禁止单方面陈述；\n"
        "   (c) 人物刻画与场景渲染：场景细节（光影、天气、物件、氛围）须随情节【实时变化】，并与人物状态互相映衬（如愤怒时烛火骤暗、风雪骤紧）。\n"
        "每条应能让下游动画生成直接据此还原「人物神态+台词+实时场景变化」的画面。\n"
        "示范句式：「苏清寒眉头微蹙，指节因用力而发白，压低声音：『你不要再往前了。』林尘不退反进，"
        "袖中逆鳞隐隐发烫；窗外骤雨倾盆，烛火被穿堂风压得忽明忽暗，映得二人面容阴晴不定。」"
        "禁止罗列关键词或空泛分点，杜绝「两人交谈/他修炼突破」这类无神态、无台词、无场景变化的概述。\n"
        "3. camera_movement（镜头运转）：字符串数组，每个元素对应一条镜头语言，单条不少于 60 字。必须包含："
        "景别（远景/全景/中景/近景/特写/大特写）、运镜方式（推/拉/摇/移/跟/升/降/旋转）、转场建议（切/叠化/淡入淡出/匹配剪辑）以及构图侧重点（主体位置/视线引导）。可结合 Markdown 分项说明，并解释该镜头如何服务于情绪或叙事。\n"
        "4. duration_estimate（预计时长）：单个字符串，给出整个改编片段的预计总时长（如「约 2 分 30 秒」），并简要说明依据，例如镜头数量、平均时长、对话密度等。"
        "硬性约束：单集总时长最长不得超过 5 分钟，若按正文规模估算会超限，必须压缩镜头数量或合并场景使总时长落在 5 分钟以内（建议 2-5 分钟区间）。\n"
        "【原文还原与精简规则——最高优先级】\n"
        "本任务的核心目标是「基本还原原书内容」，而非自由改编或摘要。务必遵守：\n"
        "   (1) 剧情安排（plot_arrangement）必须以【章节正文】为唯一事实来源，逐段、逐情节还原原书脉络，"
        "保留原书的关键对话、人物行动、转折与结局走向；禁止凭空添加正文没有的情节或改变原意。\n"
        "   (2) 章节正文中的【正文叙述】必须尽量保真还原，不得为了「简练」而省略主要情节；"
        "只有【无关内容】才可删减或压缩。\n"
        "   (3) 无关内容的定义：出场角色列表中标注「本章出场 < 2 次」的配角，其相关的大量场景铺陈、"
        "大段落环境描写、与主线无关的支线铺叙，均可大幅压缩为 1 句带过；其余角色的情节与场景则须还原保真。\n"
        "   (4) 严禁把多条独立情节合并成笼统概述，也严禁用「随后/之后他们继续前行」之类的空写替代原文具体情节。\n"
        "严格要求：只输出 JSON，不要任何解释文字。字段名必须完全一致：prev_chapter_review（字符串）/ scene_design / plot_arrangement / camera_movement（均为字符串数组） / duration_estimate（字符串）。"
        "JSON 必须合法，所有字符串用双引号，数组元素用逗号分隔，禁止尾随逗号。描述必须具体、可落地，禁止空泛词汇。\n"
        "各数组长度必须与章节份量充分匹配：scene_design 不少于 4 条、plot_arrangement 不少于 4 条、camera_movement 不少于 4 条，"
        "内容详实、互不重复，确保每集分镜细节充足、字数充分。"
    )

    # JSON 字段名常量，供解析与兜底复用，避免硬编码散落
    KEYS = ("prev_chapter_review", "scene_design", "plot_arrangement", "camera_movement", "duration_estimate")

    @classmethod
    def build_user_prompt(cls, d, char_block: str, full_text: str, prev_text: str | None = None) -> str:
        """按 drama 实体拼装用户提示：标注开篇/后续，附带上一章正文、本章章节正文与出场角色（含本章出场次数）。"""
        parts = [
            f"【小说名】{d.title}",
            f"【章节范围】第 {d.chapter_from} 章（单集漫剧片段，本集属{'开篇' if d.chapter_from == 1 else '后续/中段'}部分）",
            f"【出场角色（括号内为其在【章节正文】中的出场次数，<2 次者视为无关配角，其冗长场景/环境描写应压缩）】\n{char_block}",
        ]
        if prev_text:
            parts.append(f"【上一章正文（请据此生成 prev_chapter_review 上集回顾 150–300 字）】\n{prev_text[:6000]}")
        parts.append(f"【章节正文（本集需基本还原的内容）】\n{full_text[:16000]}")
        parts.append("请基于以上内容生成漫剧分镜 JSON，先输出 prev_chapter_review 上集回顾，再输出四段式；注意本集与上一章的剧情衔接。")
        return "\n\n".join(parts)

    @classmethod
    def fallback(cls, drama_title: str) -> dict:
        """LLM 不可用时的兜底结构化结果，保证前端仍可按四段式展示。"""
        return {
            "prev_chapter_review": "",
            "scene_design": [f"「{drama_title}」开场场景：选取章节核心冲突地点作为主舞台，突出环境氛围与人物关系。"],
            "plot_arrangement": ["林尘垂眸不语，指节因攥紧而发白，声音发颤：「……我还能撑住。」苏清寒抬手轻按住他肩头，掌心微凉，低声道：「别逞强，先缓一缓。」二人于演武场暮色中无声对峙，远山染血、风卷残叶，烛火被夜风压得忽明忽暗，映得林尘面容阴晴不定——刻画隐忍与师徒羁绊，场景随情绪由明转暗。"],
            "camera_movement": ["以中近景为主刻画人物微表情，关键冲突切换为手持跟拍增强临场感。"],
            "duration_estimate": "约 8–12 分钟（依据：所选章节正文规模与情节密度估算）",
            "content_raw": None,
        }


def _safe_list(v) -> list:
    """把模型返回的列表字段规整为字符串列表。"""
    if isinstance(v, list):
        return [str(x) for x in v if x not in (None, "")]
    return []


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
    ch_map = {ch.chapter_no: ch for ch in chapters}
    # 单章片段：取本集章节正文（chapter_from == chapter_to，create_drama 已强制单章）
    full_text = (ch_map.get(d.chapter_from).content if ch_map.get(d.chapter_from) else "")
    if not full_text:
        raise BizError(400, "该章节暂无正文内容，无法生成漫剧场景")
    # 上一章正文（供导演生成「上集回顾」）；开篇无上一章则为 None
    prev_text = (ch_map.get(d.chapter_from - 1).content if d.chapter_from > 1 and ch_map.get(d.chapter_from - 1) else None)

    chars = await _character_summaries(session, d.novel_id, d.character_ids or [])
    # 计算每个出场角色在本章正文中的出场次数，标注「无关配角」（<2 次）供导演压缩判断
    char_block = "、".join(
        f"{c['name']}（{c.get('role') or '角色'}，本章出场 {full_text.count(c['name'])} 次）"
        for c in chars
    ) or "（无已匹配角色）"

    # 标记生成中（持久化到 extra.generating），供前端刷新后识别「任务进行中」并置灰按钮
    d.extra = dict(d.extra or {})
    d.extra["generating"] = True
    await session.commit()

    user_prompt = DirectorPrompt.build_user_prompt(d, char_block, full_text, prev_text)

    result = None
    try:
        cfgs = await llm_repo.list_for_dispatch(session, owner_id, "chat")
        if not cfgs:
            raise BizError(400, "尚未配置对话模型，请先在模型管理中添加 chat 类型模型")
        # 选用默认高质量模型（list_for_dispatch 已按 is_default/weight 排序，首项即最优配置），
        # 不再优先本地 Ollama——本地 7B 模型能力弱，难以产出充分细节与字数，是「分镜粗略、字数过少」的主因。
        # 若需走本地模型，请在模型管理中将某 Ollama 配置设为默认。
        cfg = cfgs[0]
        adapter = llm_adapters.get_adapter(cfg, None)
        messages = [
            {"role": "system", "content": DirectorPrompt.SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
        # 服务端超时保护：远程大模型偶发缓慢，若超过 300s 仍未返回，
        # 主动取消并走兜底，确保 finally 能及时清除 generating 标记，
        # 避免前端刷新后永久停留在「生成中」置灰态（前端超时 180s，略大于此值）。
        try:
            raw = await asyncio.wait_for(adapter.chat(messages, json_mode=True), timeout=300)
        except (asyncio.TimeoutError, Exception) as te:
            result = DirectorPrompt.fallback(d.title)
        else:
            result = _parse_director_json(raw)
            result["content_raw"] = raw
        # 生成即落库：把导演 Agent 产出（含兜底结果）直接持久化到 story_chapter_drama_scene，
        # 防止前端刷新后丢失已生成内容；不再依赖单独的「保存」点击。
        await _persist_scene(session, owner_id, d, result, commit=False)
        return result
    except BizError:
        raise
    except Exception as e:  # LLM 调用失败时兜底，保证前端可用
        result = DirectorPrompt.fallback(d.title)
        await _persist_scene(session, owner_id, d, result, commit=False)
        return result
    finally:
        # 无论成功/失败/异常，结束生成都清除进行中标记
        d.extra = dict(d.extra or {})
        d.extra["generating"] = False
        await session.commit()


async def _persist_scene(
    session: AsyncSession, owner_id: int, d: ChapterDrama,
    parsed: dict, commit: bool = True,
) -> ChapterDramaScene:
    """把导演 Agent 四段式结果落库（upsert）。供「生成即保存」与手动「保存」复用。"""
    scene = ChapterDramaScene(
        drama_id=d.id, owner_id=owner_id, novel_id=d.novel_id,
        prev_chapter_review=str(parsed.get("prev_chapter_review") or "").strip() or None,
        scene_design=_safe_list(parsed.get("scene_design")),
        plot_arrangement=_safe_list(parsed.get("plot_arrangement")),
        camera_movement=_safe_list(parsed.get("camera_movement")),
        duration_estimate=str(parsed.get("duration_estimate") or "") or None,
        content_raw=parsed.get("content_raw"),
    )
    saved = await drama_repo.upsert_scene(session, scene)
    if commit:
        await session.commit()
    return saved


async def get_director_status(session: AsyncSession, drama_id: int, owner_id: int) -> dict:
    """查询导演生成是否进行中（M15.5）：供前端刷新后轮询，识别「任务进行中」以置灰按钮。"""
    d = await drama_repo.get(session, drama_id)
    if not d or d.owner_id != owner_id or d.deleted_at is not None:
        raise BizError(404, "章节漫剧不存在")
    return {"generating": bool((d.extra or {}).get("generating", False))}


def _recover_array(raw: str, key: str) -> list:
    """当模型把数组误并入其他字段导致 JSON 解析缺失时，从原始文本正则抢救数组内容。"""
    import re
    # 匹配 "key": [ ... ]，容忍换行与嵌套引号
    m = re.search(r'"%s"\s*:\s*\[(.*?)\]' % re.escape(key), raw, re.S)
    if not m:
        return []
    body = m.group(1)
    # 抽取每个被双引号包裹的元素（允许内部转义引号）
    items = re.findall(r'"((?:[^"\\]|\\.)*)"', body)
    return [i.replace('\\"', '"').strip() for i in items if i.strip()]


def _clean_plot_tail(p: str) -> str:
    """清理 plot 末条里被误并入的 camera_movement 字段残片。"""
    import re
    return re.sub(r',?\s*"?camera_movement"?\s*:\s*\[.*$', '', p, flags=re.S).strip()


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
                return DirectorPrompt.fallback("当前漫剧")
        else:
            return DirectorPrompt.fallback("当前漫剧")
    plot = _safe_list(obj.get("plot_arrangement"))
    camera = _safe_list(obj.get("camera_movement"))
    # 7B 模型偶发把 camera_movement 内容误并入 plot_arrangement 末条（JSON 错位），
    # 此处做格式兜底：camera 为空时，从原始输出正则抢救 camera_movement 数组；
    # 并清理 plot 末条里混入的 camera 字段残片。
    if not camera and raw:
        camera = _recover_array(raw, "camera_movement") or camera
    if plot and camera:
        plot = [_clean_plot_tail(p) for p in plot]
    return {
        "prev_chapter_review": str(obj.get("prev_chapter_review") or "").strip(),
        "scene_design": _safe_list(obj.get("scene_design")),
        "plot_arrangement": plot,
        "camera_movement": camera,
        "duration_estimate": str(obj.get("duration_estimate") or ""),
        "content_raw": raw,
    }


async def save_drama_scene(
    session: AsyncSession, owner_id: int, drama_id: int, payload: dict
) -> dict:
    """保存导演 Agent 场景分析（M15.5）：upsert 到 story_chapter_drama_scene。

    与「生成即保存」共用 _persist_scene；保留该接口以便用户手动覆盖/重存。
    """
    d = await drama_repo.get(session, drama_id)
    if not d or d.owner_id != owner_id or d.deleted_at is not None:
        raise BizError(404, "章节漫剧不存在")
    saved = await _persist_scene(session, owner_id, d, payload, commit=True)
    return _scene_to_detail(saved)


def _scene_to_detail(s: ChapterDramaScene) -> dict:
    return {
        "drama_id": s.drama_id,
        "prev_chapter_review": s.prev_chapter_review,
        "scene_design": s.scene_design or [],
        "plot_arrangement": s.plot_arrangement or [],
        "camera_movement": s.camera_movement or [],
        "duration_estimate": s.duration_estimate,
        "content_raw": s.content_raw,
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
