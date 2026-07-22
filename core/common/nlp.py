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

# 时间表达式正则（M6 实体类型扩展：time_period 候选）
_TIME_RE = re.compile(
    r"(?:公元前)?\d+年(?:\d+月(?:\d+日)?)?|公元\d+年|\d+世纪|"
    r"太古|上古|远古|洪荒|纪元[\u4e00-\u9fa5]{1,6}|[\u4e00-\u9fa5]{1,5}年间|上古时期"
)

# 中文章节标题正则（如「第一章」「第3回」）
_CHAPTER_RE = re.compile(r"第[一二三四五六七八九十百千零0-9]+[章回节卷部]")


def _ensure_jieba():
    """单例初始化 jieba；可选加载同目录 novel_dict.txt 用户词典（生造人名如「林尘」）。"""
    global _jieba_ready
    if _jieba_ready:
        return
    try:
        _dict = os.path.join(os.path.dirname(__file__), "novel_dict.txt")
        if os.path.exists(_dict):
            jieba.load_userdict(_dict)
    except Exception:
        # 词典缺失/加载失败不影响基础分词，仅召回略降
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


def extract_person_candidates(text: str, min_freq: int = 2) -> list[dict]:
    """基于词性 nr/nr1/nr2 + 规则产出候选人物/实体名单。

    返回 [{name, freq, first_chapter, entity_type}]，entity_type 默认 'character'。
    freq 为词在全文中出现次数；first_chapter 记录首次出现的章节序号。
    """
    _ensure_jieba()
    freq: dict[str, int] = {}
    first_ch: dict[str, int] = {}
    for ch in split_chapters(text):
        # pseg.cut 的每元素是 (word, flag) 二元组，解包后 w 已是词字符串
        for w, flag in pseg.cut(ch["text"]):
            if flag in _PERSON_FLAGS and _is_valid_name(w):
                name = w
                freq[name] = freq.get(name, 0) + 1
                if name not in first_ch:
                    first_ch[name] = ch["index"]
    candidates = [
        {"name": n, "freq": c, "first_chapter": first_ch.get(n), "entity_type": "character"}
        for n, c in freq.items() if c >= min_freq
    ]
    candidates.sort(key=lambda x: x["freq"], reverse=True)
    return candidates


def build_cooccurrence(text: str, window: int = 80) -> list[dict]:
    """滑动窗口共现：窗口内同时出现的人物对计权重。

    按章分别计共现避免跨章误连；返回 [{source, target, weight}]（无向去重，source<target 排序）。
    """
    candidates = {c["name"] for c in extract_person_candidates(text, min_freq=1)}
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


def extract_named_entities(text: str, min_freq: int = 2) -> dict:
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
