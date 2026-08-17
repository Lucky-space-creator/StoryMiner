"""
统一提示词中心（M 全模块 prompt 归集）

整体思路：
    把分散在 writing_service / drama_service / chapter_analysis_service / character_service /
    graph_service 等模块里的 LLM 提示词（system / user 模板、描述映射、导演 Agent 四段式类）
    集中到本文件，避免提示词散落各处难以维护；各 service 改为 `from prompts import ...` 引用。

关键点：
    1. 纯字符串模板保持原样（含 {xxx} 占位），调用方用 .format() / .replace() / f-string 注入。
    2. 需要拼装的提示词提供 build_* 函数，返回完整 content 字符串，service 不再内联长串。
    3. drama 的 DirectorPrompt 为带方法的类（SYSTEM / fallback / build_user_prompt），整类迁移。

实现逻辑：
    仅常量与纯函数，无外部依赖（除 drama 的 build_user_prompt 接收实体参数）；可被任意 service 安全导入，
    不引入循环依赖（本模块不反向 import service）。
"""
from typing import Iterable


# ===========================================================================
# 续写与概览（writing_service）
# ===========================================================================
# 风格 / 长度 / 视角 中文描述映射（M8.5）
STYLE_DESC = {
    "original": "贴合原作风格，保持既有叙事语气",
    "tense": "紧张悬疑，节奏紧凑，制造悬念",
    "warm": "温情舒缓，细腻柔和",
}
LENGTH_DESC = {
    "short": "约200字",
    "mid": "约500字",
    "long": "约1000字",
}
POV_DESC = {
    "third": "第三人称（全知或限知）视角",
    "first": "第一人称视角",
}

# 概览/时间线/角色弧线「详略」提示映射（brief/detail 各自文案）
DETAIL_HINT = {
    "summary": {
        "brief": "用精炼的要点概括（每卷/关键节点 1-2 句）",
        "detail": "充分展开，逐章细化情节脉络与转折，篇幅较长",
    },
    "timeline": {
        "brief": "仅列出关键时间节点（简短）",
        "detail": "详细列出事件，含前因后果与章节出处，篇幅较长",
    },
    "character_arc": {
        "brief": "每位人物 1 段精炼概括",
        "detail": "每位人物充分展开（起点→转折→当前状态），含关键事件",
    },
}


def build_summary_prompt(novel_name: str, text: str, detail: str = "brief") -> str:
    """情节概览 user 提示（M8.1）。"""
    hint = DETAIL_HINT["summary"].get(detail, DETAIL_HINT["summary"]["brief"])
    return (
        f"你是一位资深小说编辑。请阅读小说《{novel_name}》的章节内容，生成一份【情节概览】。\n"
        "要求：按章节/卷梳理故事线，提炼每条情节的核心事件与推进，语言简练、有层次。\n"
        f"详略程度：{hint}。\n"
        f"=== 正文（节选）===\n{text}\n=== 结束 ===\n"
        "请直接输出概览，使用 Markdown 列表或分段，不要添加多余解释。"
    )


def build_timeline_prompt(novel_name: str, text: str, detail: str = "brief") -> str:
    """时间线梳理 user 提示（M8.2）。"""
    hint = DETAIL_HINT["timeline"].get(detail, DETAIL_HINT["timeline"]["brief"])
    return (
        f"请基于小说《{novel_name}》正文，提取关键事件并梳理成【时间线】。\n"
        "要求：按事件发生的时间顺序排列；若原文存在时间乱序请纠正并标注；"
        "每条事件用一句话概括，可附章节出处。\n"
        f"详略程度：{hint}。\n"
        f"=== 正文（节选）===\n{text}\n=== 结束 ===\n"
        "直接输出时间线（时间 → 事件 的列表形式），不要添加多余解释。"
    )


def build_character_arc_prompt(novel_name: str, chars_text: str, detail: str = "brief") -> str:
    """角色弧线 user 提示（M8.3）。"""
    hint = DETAIL_HINT["character_arc"].get(detail, DETAIL_HINT["character_arc"]["brief"])
    return (
        f"以下是小说《{novel_name}》的主要人物档案：\n{chars_text}\n\n"
        "请结合上述人物，生成【角色弧线概览】：概述每位主要人物的成长/变化轨迹"
        "（起点状态 → 关键转折 → 当前状态）。\n"
        f"详略程度：{hint}。\n"
        "直接输出，按人物分小节，不要添加多余解释。"
    )


def build_continue_system(context: str, user_prompt: str, style: str, length: str, pov: str, char_block: str = "") -> str:
    """续写 system 提示（M8.4/M8.5）。

    参数：context 前文末尾片段；user_prompt 用户提示词；char_block 结合人物设定时的注入块（空串不影响拼接）。
    """
    return (
        "你是一位小说续写助手。请根据【前文】与【创作要求】继续创作后续情节。\n"
        f"【前文】（末尾片段）：\n{context}\n"
        f"【用户提示】：{user_prompt or '（无，请自然延续）'}\n"
        "【创作要求】：\n"
        f"- 文风：{STYLE_DESC.get(style, STYLE_DESC['original'])}\n"
        f"- 篇幅：{LENGTH_DESC.get(length, LENGTH_DESC['mid'])}\n"
        f"- 视角：{POV_DESC.get(pov, POV_DESC['third'])}\n"
        "- 严格延续前文的人物、世界观与叙事节奏；不要重复前文结尾，自然衔接展开。\n"
        f"{char_block}"
        "直接输出续写正文，不要加任何解释、标题或前缀。"
    )


# ===========================================================================
# 章节漫剧导演 Agent（drama_service，整类迁移）
# ===========================================================================
class DirectorPrompt:
    """导演 Agent（M15.5）提示词集中管理：系统提示、用户提示、LLM 不可用兜底。

    整体思路：
        把原本零散在各处的提示词集中为类：SYSTEM 定义角色/四段式字段约束；
        build_user_prompt 按 drama 实体拼装用户提示；fallback 在 LLM 不可用时返回结构化四段式。

    关键点：
        1. SYSTEM 明确要求「字数充足、细节丰富」，取消对弱模型的迁就。
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


# ===========================================================================
# 章节解析（chapter_analysis_service）
# ===========================================================================
STRUCTURE_SYSTEM = """你是一位资深的小说编辑与出版专家，擅长分析小说的文档结构与章节组织。"""

STRUCTURE_USER = """请分析以下小说文档的整体结构。

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


CHAPTER_SYSTEM = """你是一位资深的小说内容分析专家，擅长对小说章节进行结构化解析。"""

CHAPTER_USER = """请分析以下小说章节。

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


CHAPTER_BATCH_USER = """请分析以下小说的连续 {batch_size} 个章节，一次性返回 JSON 数组。

【小说全局】
名称：《{novel_name}》
简介：{novel_summary}
全书总章节数：{total}

{chapters_block}

【输出要求】
只输出一个 JSON 数组（长度严格为 {batch_size}），每个元素对应上面一章，结构如下：
{{
  "chapter_index": <整数，必须与该章序号一致>,
  "chapter_title": "<标题>",
  "type": "content",
  "summary": "80字以内情节摘要，禁止模板套话",
  "key_events": ["2-4个关键情节事件"],
  "characters_appeared": ["最多5个出场人物"],
  "locations": ["最多3个地点"],
  "plot_role": "开篇铺垫/日常过渡/冲突爆发/高潮/转折/收尾/伏笔埋设",
  "emotional_tone": "轻松/紧张/悲情/热血/温馨/悬疑/恐怖/平淡",
  "climax_sentence": "最精彩一句（可选）",
  "connections": "前后章承接关系"
}}
仅输出 JSON 数组本身，不要 markdown 标记，不要任何解释文字。"""


# ===========================================================================
# 人物小传（character_service）
# ===========================================================================
GEN_PROMPT = """你是小说人物小传撰写助手，请基于给定文本为该人物生成结构化档案。
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


# ===========================================================================
# 知识图谱抽取（graph_service）
# ===========================================================================
EXTRACT_SYSTEM = """你是一个专业的小说知识图谱抽取引擎，请严格按以下规范从文本中抽取实体与关系。

## 实体类型定义（共7种，必须使用指定 code）

1. **character（人物）**：小说中登场的人物角色，含主角、配角、龙套、提到名字但未出场的历史/传说人物
2. **place（地点）**：场景、地理位置、建筑、城池、国家、区域、洞府、秘境
3. **org（组织）**：帮派、宗门、势力、家族、王朝、机构、队伍
4. **time_period（时间）**：具体时间点、时间段、历史时期、朝代纪年、人物年龄、事件持续时长
5. **event（事件）**：发生的重要事件、情节转折、战斗、会议、仪式、比武、突破、死亡
6. **item（物品）**：重要道具、武器、法宝、丹药、秘籍、信物、货币、材料
7. **concept（概念）**：功法体系、修炼境界、世界观设定、特殊规则、文化概念、种族

## 抽取规范
- name 必须使用原文中的准确名称，不要杜撰
- profile 为 JSON 对象，记录关键属性（如：{"身份":"主角","性别":"男","境界":"筑基期"}）
- description 用一句简短中文概括该实体在原文中的作用
- 每个实体至少要有一个有意义的关系连接，孤立节点可忽略

## 输出格式
严格输出一个 JSON 对象，不要包含任何解释或 markdown 标记：
{
  "entities": [
    {"name":"张三","type":"character","profile":{"身份":"主角"},"description":"小说主人公"},
    {"name":"天剑宗","type":"org","profile":{"类型":"宗门"},"description":"主角所在宗门"}
  ],
  "relations": [
    {"source":"张三","target":"天剑宗","type":"所属宗门","evidence":"原文证据句"}
  ]
}"""

EXTRACT_USER = "请从以下小说文本中抽取所有实体和关系（共7种类型：character/place/org/time_period/event/item/concept）：\n\n{text}"

# 实体抽取提示（仅实体，不含关系输出，省 token）
ENTITY_SYSTEM = """你是小说知识图谱实体抽取引擎，请严格按规范从文本抽取7种实体类型。

## 实体类型（共7种，必须使用指定 code）
1. character(人物) 2. place(地点) 3. org(组织) 4. time_period(时间)
5. event(事件) 6. item(物品) 7. concept(概念)

## 规范
- name 用原文准确名称，勿杜撰
- profile 为 JSON 对象记录关键属性（如 {"身份":"主角","境界":"筑基期"}）
- description 用一句简短中文概括该实体在原文中的作用
- 仅抽取实体，不要输出关系（关系将另由共现分析定性）

## 输出格式（严格 JSON，无 markdown、无解释）
{"entities":[{"name":"张三","type":"character","profile":{"身份":"主角"},"description":"小说主人公"}]}"""

ENTITY_USER = """请从以下小说文本抽取全部实体（7种类型）。
下列是由确定性预分析给出的候选实体（仅供参考，请核实原文后抽取，勿漏抽其他实体）：
{candidates}

文本：
{text}"""

# 关系定性提示（每条候选边一次 LLM 调用）
REL_QUAL_SYSTEM = """你是小说关系定性助手。给定两个频繁共现的实体 A 与 B 及原文片段，判断二者关系。
仅输出一个 JSON 对象，不要 markdown、不要解释：
{"type":"关系类型","direction":"A->B"|"B->A"|"none","evidence":"一句原文证据"}
- type 用简洁中文（如 师徒/敌对/同门/父子/主仆/夫妻/上下级/挚友/仇敌）
- direction：若关系有方向（如 A 是 B 的师父）则 A->B；反之 B->A；无向则 none"""

REL_QUAL_USER = """小说中「{a}」与「{b}」频繁共同出现。请基于以下原文片段判断二者关系：
{ctx}"""
