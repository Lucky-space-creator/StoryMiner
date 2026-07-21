"""
章节切分器

整体思路：
    用正则识别常见章节标题（支持 Markdown ## 第X章 / 纯文本第X章 / Chapter X 等），
    将原文切分为带编号的章节条目。

关键点：
    1. 支持 Markdown 标题格式：# 第一章 / ## 第一章 标题名
    2. 支持纯文本格式：第X章 / Chapter X / Volume X
    3. 记录每章在原文中的字符区间（char_start/char_end）。
    4. 无标题时整体作为单章。
    5. 标题取整行内容（包含副标题如「第一章 废柴」），过滤掉开头的 # 号。

实现逻辑：
    扫描所有标题匹配位置，按相邻标题切分；末段到文末。
"""
import re

# 章节标题正则：行首可选空白或 # 号，后跟第X章/Chapter X 等
# 关键改进：去掉 \b（与中文不兼容），允许 Markdown # 前缀，捕获整行标题
_TITLE_RE = re.compile(
    r"^[\s#]*(?:"
    # 中文：第N章/卷/节/回/部/篇 — 支持中文数字和阿拉伯数字
    r"第\s*[一二三四五六七八九十百千零\d]+\s*[章卷节回部篇]"
    r"|"
    # 英文：Chapter X / Volume X
    r"Chapter\s+\d+"
    r"|"
    r"Volume\s+\d+"
    r")",
    re.MULTILINE
)

# 提取标题行剩余内容：跳过开头的 # 号和空白
_TITLE_CLEAN = re.compile(r'^[\s#]*')


def _extract_title(line: str) -> str:
    """从匹配行中提取标题：去除开头的 # 和空白，取整行。"""
    # 先去掉前缀 # 号
    title = _TITLE_CLEAN.sub('', line)
    # 去掉首尾空白，截断到合理长度
    return title.strip()[:200]


def split_text(text: str) -> list[dict]:
    """将正文切分为章节列表，每项为 {title, content, word_count, char_start, char_end}。

    整体思路：
        1. 扫描所有匹配位置（字符串偏移量）。
        2. 相邻两个标题之间为一个章节内容。，末段到文末。
        3. 标题取匹配行的整行内容（去掉 # 前缀）。
        4. 无匹配时返回单章。

    关键点：
        避免将正文中的「第X回」误判为标题——仅匹配以空白或 # 开头的行首。
    """
    matches = list(_TITLE_RE.finditer(text))
    if not matches:
        stripped = text.strip()
        return [{
            "title": None, "content": stripped,
            "word_count": len(stripped), "char_start": 0, "char_end": len(text),
        }]

    chapters = []
    for i, m in enumerate(matches):
        # 从匹配位置开始（即行首），提取整行作为标题
        line_start = text.rfind('\n', 0, m.start()) + 1 if m.start() > 0 else 0
        line_end = text.find('\n', m.end())
        if line_end == -1:
            line_end = len(text)
        full_line = text[line_start:line_end]
        title = _extract_title(full_line)

        # 跳过明显不是章节标题的误匹配：
        # 标题过长（>40字）→ 可能是正文中的叙术文字
        if len(title) > 40:
            continue

        # 内容区间：从标题行结束到下一章标题开始（或文末）
        content_start = line_end + 1 if line_end < len(text) else len(text)
        if i + 1 < len(matches):
            # 下一章标题的行首
            next_line_start = text.rfind('\n', 0, matches[i + 1].start())
            content_end = next_line_start + 1 if next_line_start >= 0 else matches[i + 1].start()
        else:
            content_end = len(text)

        content = text[content_start:content_end].strip()

        chapters.append({
            "title": title,
            "content": content,
            "word_count": len(content),
            "char_start": line_start,
            "char_end": content_end,
        })

    return chapters
