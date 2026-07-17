"""
文本切割引擎（M3 切割+向量化）

整体思路：
    将文档原文按章节边界切分，再按策略（chapter/length）切成检索单元，供 M3 向量化与 M4 展示复用。

关键点：
    1. 复用 M1 的 split_text 得到章节结构，保证与解析切章一致。
    2. strategy=chapter：每章一个切片（超长则按 length 兜底拆分）。
    3. strategy=length：固定窗口 size 字符、overlap 重叠滑动切片。
    4. 返回结构化片段含章节标题与字符区间，写入 chunk.meta 供 M4 来源定位。

实现逻辑：
    纯函数无状态；_split_length 实现滑动窗口；chunk_document 编排章节→片段。
"""
from parsers.chapter_splitter import split_text


def _split_length(text: str, size: int, overlap: int) -> list[str]:
    """按长度滑动窗口切片（size>0，overlap<size）。"""
    if size <= 0:
        size = 800
    if overlap < 0 or overlap >= size:
        overlap = 0
    pieces: list[str] = []
    step = size - overlap
    if not text.strip():
        return pieces
    for i in range(0, len(text), step):
        piece = text[i:i + size].strip()
        if piece:
            pieces.append(piece)
        if i + size >= len(text):
            break
    return pieces or [text.strip()]


def chunk_document(text: str, strategy: str = "length", size: int = 800, overlap: int = 0) -> list[dict]:
    """文档级切割：切章→按策略切片，返回片段列表。

    每个片段: {title, content, char_start, char_end}
    """
    chapters = split_text(text) or [{"title": "正文", "content": text}]
    out: list[dict] = []
    for ch in chapters:
        content = ch.get("content") or ""
        if not content.strip():
            continue
        if strategy == "chapter":
            pieces = _split_length(content, size or 1500, overlap or 0) if len(content) > (size or 1500) else [content]
        else:
            pieces = _split_length(content, size or 800, overlap or 0)
        start = 0
        for p in pieces:
            out.append({
                "title": ch.get("title", ""),
                "content": p,
                "char_start": start,
                "char_end": start + len(p),
            })
            start += len(p)
    return out
