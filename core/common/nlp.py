"""
确定性预处理层（混合分析管道 M2 / P0）

整体思路：
    在 LLM 介入前用 jieba 零成本产出人物候选、共现矩阵、文本指标，
    收窄后续 LLM 输入，落实「确定性处理 80%、LLM 仅 20%」。

关键点：
    1. 单例加载 jieba，避免重复初始化；可选加载 novel_dict.txt 用户词典提升人名召回。
    2. extract_person_candidates 基于词性 nr/nr1/nr2 + 规则产出候选名单与频次。
    3. build_cooccurrence 用滑动窗口产出候选关系边（带权重），供图谱 P3 定性。
    4. text_metrics 产出句长方差 / 对话密度 / TTR，供章节分析 P1 注入。
    5. split_chapters 仅用于本层确定性计算（共现/指标按章隔离），不替代 service 层现有章节导航。

实现逻辑：
    模块加载时 _ensure_jieba 单例初始化（及可选词典）；各函数纯本地计算，无 LLM 调用、无 DB 依赖。
"""
import os
import re
import threading

import jieba
import jieba.posseg as pseg

# 单例初始化标志
_jieba_ready = False

# 过滤用停用词（极简，避免常见虚词被误判为人名）
_STOP = set("的 了 是 在 我 你 他 她 它 们 这 那 有 和 与 及 也 都 就 而 等 着 过 又 个 之 其".split())

# 人名词性集合（jieba 标注）
_PERSON_FLAGS = {"nr", "nr1", "nr2", "nrf", "nrj"}

# 地名/机构名词性集合（jieba 标注，M6 实体类型扩展）
_PLACE_FLAGS = {"ns", "nsf"}
_ORG_FLAGS = {"nt"}

# 候选频率阈值常量（统一管理，避免魔法数散落各处）：
#   MIN_FREQ_DEFAULT=2  —— 通用默认，过滤偶现噪声词
#   MIN_FREQ_LOW=1      —— 放宽（共现/低召回场景，保留所有出现过的词）
#   MIN_FREQ_STRICT=10  —— 严格（精析前大幅收窄，仅保留高频主角，降低 LLM 调用量）
MIN_FREQ_DEFAULT = 2
MIN_FREQ_LOW = 1
MIN_FREQ_STRICT = 10

# ── 人物精析提示词（P1-9 统一来源：graph 与 character_service 共用，避免双份实现漂移） ──
CANDIDATE_PROMPT = """你是小说人物小传撰写助手。请根据提供的小说片段，为【指定人物】生成结构化档案。
仅输出一个 JSON 对象，不要包含任何解释或 markdown 标记，格式严格如下：
{
  "role":"主角/配角/反派",
  "gender":"男/女/未知",
  "identity":"身份或职业",
  "personality":"性格特点",
  "appearance":"外貌特征",
  "catchphrase":"口头禅",
  "description":"150字以内的人物小传，包含大致经历"
}
人物姓名：{name}
相关小说片段：
{text}"""

# jieba 词典加载/修改的全局锁：防止多 worker 并发 add_word/del_word 互相污染（P2-14）
_jieba_lock = threading.Lock()


def _read_dict_words(path: str) -> list[str]:
    """读取用户词典文件，返回词列表（仅取首列词，忽略词性/频次）。"""
    words = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if parts and not parts[0].isascii():
                    words.append(parts[0])
    except Exception:
        pass
    return words


def _apply_user_dict(words):
    """临时向 jieba 注入用户词典词（进程级，需与 _remove_user_dict 配对）。"""
    for w in words:
        try:
            jieba.add_word(w)
        except Exception:
            pass


def _remove_user_dict(words):
    """清除临时注入的用户词典词，避免污染其他小说（P2-14 按 novel 隔离）。"""
    for w in words:
        try:
            jieba.del_word(w)
        except Exception:
            pass

# 时间表达式正则（M6 实体类型扩展：time_period 候选）
_TIME_RE = re.compile(
    r"(?:公元前)?\d+年(?:\d+月(?:\d+日)?)?|公元\d+年|\d+世纪|"
    r"太古|上古|远古|洪荒|纪元[\u4e00-\u9fa5]{1,6}|[\u4e00-\u9fa5]{1,5}年间|上古时期"
)

# 中文章节标题正则（如「第一章」「第3回」）
_CHAPTER_RE = re.compile(r"第[一二三四五六七八九十百千零0-9]+[章回节卷部]")


def _load_novel_dict():
    """加载用户词典（生造人名如「林尘」），提升人名召回。

    整体思路：词典路径按优先级探测（环境变量 NOVEL_DICT_PATH > 同目录 novel_dict.txt >
    上级 core 目录 > 当前工作目录），命中即加载；加载失败仅告警不阻断分词。

    关键点（P2-14/15/16/17 防御）：
        1. 路径可配置且多候选回退，避免部署目录差异导致词典丢失。
        2. 空词典/缺失：静默跳过，基础分词仍可用（召回略降）。
        3. 行级容错：逐行读取，跳过空行与非法行（非「词 词性 [频次]」格式），
           避免因单行格式错误导致整本词典加载失败。
    """
    candidates = [
        os.getenv("NOVEL_DICT_PATH"),  # 环境变量优先
        os.path.join(os.path.dirname(__file__), "novel_dict.txt"),       # 同目录（默认）
        os.path.join(os.path.dirname(__file__), "..", "novel_dict.txt"), # 上级 core 目录
        os.path.join(os.getcwd(), "novel_dict.txt"),                     # 工作目录回退
    ]
    path = next((p for p in candidates if p and os.path.exists(p)), None)
    if not path:
        return  # 词典缺失：不阻断
    try:
        # 逐行容错读取，仅保留合法行（jieba 词典格式：词 [词性] [频次]）
        valid_lines = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                # 至少含「词」；词性应为英文 nl/nr 等（可选）；频次为数字（可选）
                if len(parts) >= 1 and not parts[0].isascii():
                    valid_lines.append(line)
        if valid_lines:
            # 写入临时纯净词典交给 jieba，避免单行非法拖垮整体
            import tempfile
            tmp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                              suffix=".txt", delete=False)
            tmp.write("\n".join(valid_lines) + "\n")
            tmp.close()
            jieba.load_userdict(tmp.name)
            os.unlink(tmp.name)
    except Exception:
        # 词典加载失败不影响基础分词，仅召回略降
        pass


def _ensure_jieba():
    """单例初始化 jieba；可选加载用户词典（生造人名如「林尘」）。"""
    global _jieba_ready
    if _jieba_ready:
        return
    try:
        _load_novel_dict()
    except Exception:
        pass
    _jieba_ready = True


def _is_valid_name(name: str) -> bool:
    """人名词基础校验：长度 2-6、非停用、非纯数字、非单字。"""
    if not name or len(name) < 2 or len(name) > 6:
        return False
    if name in _STOP:
        return False
    if re.fullmatch(r"[0-9]+", name):
        return False
    if re.fullmatch(r"[\u4e00-\u9fa5]", name):
        return False
    return True


def split_chapters(text: str) -> list[dict]:
    """按中文章节标题切分，返回 [{index, title, text}]；无标题则整体为一章。"""
    _ensure_jieba()
    matches = list(_CHAPTER_RE.finditer(text))
    if not matches:
        return [{"index": 0, "title": "", "text": text}]
    chapters = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chapters.append({"index": i, "title": m.group(0), "text": text[start:end]})
    return chapters


def segment(text: str) -> list[tuple[str, str]]:
    """jieba 分词 + 词性标注，返回 [(词, 词性), ...]。"""
    _ensure_jieba()
    return [(w.word, w.flag) for w in pseg.cut(text)]


def _extract_core(text: str, min_freq: int) -> list[dict]:
    """核心候选抽取（无词典副作用）：供全局词典与临时用户词典复用。"""
    freq: dict[str, int] = {}
    first_ch: dict[str, int] = {}
    for ch in split_chapters(text):
        for w, flag in pseg.cut(ch["text"]):
            if flag in _PERSON_FLAGS and _is_valid_name(w):
                freq[w] = freq.get(w, 0) + 1
                if w not in first_ch:
                    first_ch[w] = ch["index"]
    candidates = [
        {"name": n, "freq": c, "first_chapter": first_ch.get(n), "entity_type": "character"}
        for n, c in freq.items() if c >= min_freq
    ]
    candidates.sort(key=lambda x: x["freq"], reverse=True)
    return candidates


def extract_person_candidates(text: str, min_freq: int = MIN_FREQ_DEFAULT, user_dict=None) -> list[dict]:
    """基于词性 nr/nr1/nr2 + 规则产出候选人物/实体名单。

    返回 [{name, freq, first_chapter, entity_type}]，entity_type 默认 'character'。
    freq 为词在全文中出现次数；first_chapter 记录首次出现的章节序号。

    user_dict（P2-14 按 novel 隔离）：传入小说专属词典路径（str）或词列表（list[str]），
    仅在本次抽取期间临时注入 jieba，结束后 del_word 清除，避免污染进程级全局词典影响其他小说。
    """
    _ensure_jieba()
    if user_dict:
        words = _read_dict_words(user_dict) if isinstance(user_dict, str) else list(user_dict)
        if words:
            with _jieba_lock:
                _apply_user_dict(words)
                try:
                    return _extract_core(text, min_freq)
                finally:
                    _remove_user_dict(words)
    return _extract_core(text, min_freq)


def extract_character_context(text: str, name: str, max_chars: int = 4000) -> str:
    """取含指定人名的相关片段拼接（候选精析喂料），截断到 max_chars（P1-7 全面修正）。

    整体思路：按人名出现位置取上下文窗口拼接为喂料；修复旧版四类缺陷——
        ① 仅从头累加导致主角只覆盖前半本 → 改为分层（开头/发展/高潮/结局）配额采样；
        ② 相邻片段重叠 → 起始落入上次范围即跳步去重；
        ③ 末尾 [:max_chars] 硬切半句 → 最终回退至句末（。！？）；
        ④ 子串误匹配（「安静」命中「请安静」）→ 用 jieba.tokenize 精确词元定位，规避子串误命中。

    关键点：中文词边界无法用正则 \w 表达（\w 不含汉字，且相邻汉字无分隔）；
        故以 jieba 分词器取的「精确词元 == name」作为出现点，兼顾精确与召回。
    实现逻辑：jieba.tokenize 定位 → 四段配额采样 → 重叠跳步 → 拼接后句末截断兜底。
    """
    if not text or not name:
        return ""
    # 中文无空格分词，人名「词边界」无法用正则 \w 表达（Python \w 不含汉字，
    # 且中文相邻汉字间无分隔符，纯正则必在「召回」与「精确」间二选一、两难）。
    # 故直接复用已加载的 jieba 分词器（jieba.tokenize 返回 (词, 起, 止)），
    # 以「精确词元 == name」定位出现点：既规避「请安静」被误判为「安静」这类子串命中，
    # 又保留真实出现的召回（不会因后面跟汉字而漏匹配）。
    # 注：若 jieba 把「请安静」切成「请/安静」，则 token 仍会命中，属分词器语义噪声，
    # 对上下文喂料可接受；要更严可后续接实体对齐。
    _ensure_jieba()
    idxs = [start for word, start, _end in jieba.tokenize(text) if word == name]
    if not idxs:
        return text[:max_chars]
    n = len(text)
    segs = 4  # 开头/发展/高潮/结局
    seg_len = n / segs
    budget = max_chars // segs
    snippets: list[str] = []
    last_end = -1
    for s in range(segs):
        seg_start = int(s * seg_len)
        seg_end = n if s == segs - 1 else int((s + 1) * seg_len)
        seg_total = 0
        for i in idxs:
            if i < seg_start or i >= seg_end:
                continue
            a, b = max(0, i - 200), min(n, i + 400)
            if a <= last_end:  # 与已收集片段重叠则跳步，去重
                continue
            snippets.append(text[a:b])
            last_end = b
            seg_total += (b - a)
            if seg_total >= budget:
                break
        if sum(len(x) for x in snippets) >= max_chars:
            break
    out = "\n……\n".join(snippets)
    if len(out) > max_chars:
        cut = out.rfind("。", 0, max_chars)
        if cut == -1:
            cut = out.rfind("！", 0, max_chars)
        if cut == -1:
            cut = out.rfind("？", 0, max_chars)
        if cut == -1:
            cut = max_chars
        out = out[:cut]
    return out


def build_cooccurrence(text: str, window: int = 80) -> list[dict]:
    """滑动窗口共现：窗口内同时出现的人物对计权重。

    按章分别计共现避免跨章误连；返回 [{source, target, weight}]（无向去重，source<target 排序）。
    """
    candidates = {c["name"] for c in extract_person_candidates(text, min_freq=MIN_FREQ_LOW)}
    if not candidates:
        return []
    edges: dict[tuple[str, str], int] = {}
    for ch in split_chapters(text):
        # 仅保留出现在候选名单中的词，解包 (w, flag) 取词字符串 w
        words = [w for w, _ in pseg.cut(ch["text"]) if w in candidates]
        for i, a in enumerate(words):
            lo = max(0, i - window)
            for j in range(lo, i):
                b = words[j]
                if a == b:
                    continue
                key = (a, b) if a < b else (b, a)
                edges[key] = edges.get(key, 0) + 1
    return [{"source": k[0], "target": k[1], "weight": v}
            for k, v in edges.items() if v >= 1]


def extract_named_entities(text: str, min_freq: int = MIN_FREQ_DEFAULT) -> dict:
    """扩展实体抽取（M6 实体类型扩展）：抽 character/place/org/time 四类候选。

    返回 {type: [{name, freq}]}，其中 character 复用 extract_person_candidates，
    place/org 取 jieba 的 ns/nt 标注，time 取正则时间表达式。
    作为确定性候选提示供图谱 LLM 精析（提升地点/机构/时间召回），不强制。
    """
    chars = {c["name"]: c["freq"] for c in extract_person_candidates(text, min_freq)}
    places: dict[str, int] = {}
    orgs: dict[str, int] = {}
    for ch in split_chapters(text):
        for w, flag in pseg.cut(ch["text"]):
            if flag in _PLACE_FLAGS and _is_valid_name(w):
                places[w] = places.get(w, 0) + 1
            elif flag in _ORG_FLAGS and _is_valid_name(w):
                orgs[w] = orgs.get(w, 0) + 1
    times: dict[str, int] = {}
    for m in _TIME_RE.finditer(text):
        t = m.group(0)
        times[t] = times.get(t, 0) + 1

    def top(d: dict[str, int]) -> list[dict]:
        return [{"name": n, "freq": c} for n, c in d.items() if c >= min_freq]

    return {
        "character": [{"name": n, "freq": c} for n, c in chars.items()],
        "place": top(places),
        "org": top(orgs),
        "time": top(times),
    }


def text_metrics(text: str) -> dict:
    """返回 {sentence_len_var, dialogue_density, ttr}。

    sentence_len_var：句长方差（去空白后字符数）。
    dialogue_density：引号内字符数 / 总字符数。
    ttr：去重词数 / 总词数（词汇丰富度）。
    """
    _ensure_jieba()
    sentences = re.split(r"[。！？!?；;\n]+", text)
    sent_lens = [len(re.sub(r"\s+", "", s)) for s in sentences if s.strip()]
    if sent_lens:
        mean = sum(sent_lens) / len(sent_lens)
        var = sum((x - mean) ** 2 for x in sent_lens) / len(sent_lens)
    else:
        var = 0.0
    total_chars = len(text)
    quoted = sum(len(m) for m in re.findall(r"[\"“'『「][^\"”’』」]*[\"”’』」]", text))
    dialogue_density = (quoted / total_chars) if total_chars else 0.0
    # pseg.cut 解包 (w, flag) 取词字符串 w
    words = [w for w, _ in pseg.cut(text) if w.strip()]
    ttr = (len(set(words)) / len(words)) if words else 0.0
    return {
        "sentence_len_var": round(var, 2),
        "dialogue_density": round(dialogue_density, 4),
        "ttr": round(ttr, 4),
    }
