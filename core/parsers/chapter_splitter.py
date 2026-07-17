"""
章节切分器

整体思路：
    用正则识别常见章节标题（第X章/卷X/Chapter X 等），将原文切分为带编号的章节。

关键点：
    1. 支持中文「第…章/卷/节/回/部/篇」与英文「Chapter X」「Volume X」。
    2. 记录每章在原文中的字符区间（char_start/char_end）用于来源定位（M3.5）。
    3. 无标题时整体作为单章，标题为空。

实现逻辑：
    扫描所有标题匹配位置，按相邻标题切分；末段到文末。
"""
import re

# 行首章节标题：中文「第N章/卷/节/回/部/篇」或英文 Chapter/Volume N
_TITLE_RE = re.compile(
    r"(?m)^\s*(?:第\s*[一二三四五六七八九十百千零0-9]+\s*[章卷节回部篇]"
    r"|Chapter\s+[0-9]+"
    r"|Volume\s+[0-9]+)\b"
)


def split_text(text: str) -> list[dict]:
    """将正文切分为章节列表，每项为 {title, content, word_count, char_start, char_end}。"""
    matches = list(_TITLE_RE.finditer(text))
    if not matches:
        stripped = text.strip()
        return [{
            "title": None, "content": stripped,
            "word_count": len(stripped), "char_start": 0, "char_end": len(text),
        }]
    chapters = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        title = text[m.start():m.end()].strip()
        content = text[start:end].strip()
        chapters.append({
            "title": title,
            "content": content,
            "word_count": len(content),
            "char_start": start,
            "char_end": end,
        })
    return chapters
