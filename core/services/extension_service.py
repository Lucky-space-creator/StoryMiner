"""
扩展功能业务逻辑（M13）。

整体思路：
    编排 M13 各能力：全局搜索(M13.1)、笔记(M13.2)、阅读进度(M13.3)、标签(M13.4)、
    收藏(M13.6)、审计日志(M13.7)；对外返回贴合前端字段契约的结构，保证前端零改接通。

关键点：
    1. 全局搜索汇总 Novel/Chapter/Chunk/Character 命中，归一为 {type,title,snippet}。
    2. 关键写操作（建笔记/收藏）自动追加审计日志，体现 M13.7 操作留痕。
    3. 标签列表按前端约定返回字符串数组；创建标签做同名去重。

实现逻辑：
    薄封装 extension_repo，事务在 service 层提交；时间统一 isoformat 输出。
"""
from repositories import extension_repo


def _iso(dt):
    """datetime → ISO 字符串（None 安全）。"""
    return dt.isoformat() if dt else ""


def _snippet(text: str, kw: str, width: int = 60) -> str:
    """截取命中关键词附近的文本片段，便于搜索结果预览。"""
    if not text:
        return ""
    text = text.replace("\n", " ")
    pos = text.lower().find(kw.lower())
    if pos < 0:
        return text[:width]
    start = max(0, pos - width // 2)
    return ("…" if start > 0 else "") + text[start:start + width]


# ---------------- 全局搜索（M13.1） ----------------
async def global_search(session, owner_id: int, kw: str):
    """跨小说/章节/切片/人物统一检索，归一为 {type,title,snippet} 列表。"""
    results: list[dict] = []
    for n in await extension_repo.search_novels(session, owner_id, kw):
        results.append({"type": "小说", "title": n.name, "snippet": _snippet(n.description or "", kw)})
    for c in await extension_repo.search_chapters(session, owner_id, kw):
        results.append({"type": "章节", "title": c.title, "snippet": _snippet(c.content, kw)})
    for ck in await extension_repo.search_chunks(session, owner_id, kw):
        results.append({"type": "切片", "title": f"#{ck.idx}", "snippet": _snippet(ck.content, kw)})
    for ch in await extension_repo.search_characters(session, owner_id, kw):
        results.append({"type": "人物", "title": ch.name, "snippet": _snippet(ch.description or "", kw)})
    return results


# ---------------- 笔记（M13.2） ----------------
async def list_notes(session, owner_id: int):
    """列出笔记，返回前端契约字段。"""
    rows = await extension_repo.list_notes(session, owner_id)
    return [
        {"id": n.id, "title": n.title, "content": n.content,
         "target_type": n.target_type, "target_id": n.target_id, "updated_at": _iso(n.updated_at)}
        for n in rows
    ]


async def create_note(session, owner_id: int, data: dict):
    """新建笔记并记审计。"""
    obj = await extension_repo.create_note(session, owner_id, data)
    await extension_repo.add_audit_log(session, owner_id, "create_note", obj.title or f"note#{obj.id}")
    await session.commit()
    await session.refresh(obj)
    return {"id": obj.id, "title": obj.title, "content": obj.content, "updated_at": _iso(obj.updated_at)}


async def delete_note(session, owner_id: int, note_id: int):
    """删除笔记并记审计。"""
    await extension_repo.delete_note(session, owner_id, note_id)
    await extension_repo.add_audit_log(session, owner_id, "delete_note", f"note#{note_id}")
    await session.commit()


# ---------------- 标签（M13.4） ----------------
async def list_tags(session, owner_id: int):
    """按前端约定返回标签名字符串数组。"""
    rows = await extension_repo.list_tags(session, owner_id)
    return [t.name for t in rows]


async def create_tag(session, owner_id: int, name: str, color: str = ""):
    """新建标签，同名则复用。"""
    exist = await extension_repo.get_tag_by_name(session, owner_id, name)
    if exist:
        return {"id": exist.id, "name": exist.name, "color": exist.color}
    obj = await extension_repo.create_tag(session, owner_id, {"name": name, "color": color})
    await session.commit()
    await session.refresh(obj)
    return {"id": obj.id, "name": obj.name, "color": obj.color}


# ---------------- 收藏（M13.6） ----------------
async def list_favorites(session, owner_id: int):
    """列出收藏，返回前端契约字段。"""
    rows = await extension_repo.list_favorites(session, owner_id)
    return [
        {"id": f.id, "title": f.title, "type": f.target_type,
         "target_id": f.target_id, "at": _iso(f.created_at)}
        for f in rows
    ]


async def create_favorite(session, owner_id: int, data: dict):
    """新建收藏并记审计。"""
    obj = await extension_repo.create_favorite(session, owner_id, data)
    await extension_repo.add_audit_log(session, owner_id, "create_favorite", obj.title or f"fav#{obj.id}")
    await session.commit()
    await session.refresh(obj)
    return {"id": obj.id, "title": obj.title, "type": obj.target_type, "at": _iso(obj.created_at)}


async def delete_favorite(session, owner_id: int, fav_id: int):
    """取消收藏并记审计。"""
    await extension_repo.delete_favorite(session, owner_id, fav_id)
    await extension_repo.add_audit_log(session, owner_id, "delete_favorite", f"fav#{fav_id}")
    await session.commit()


# ---------------- 审计日志（M13.7） ----------------
async def list_audit_logs(session, owner_id: int):
    """列出审计日志，返回前端契约字段。"""
    rows = await extension_repo.list_audit_logs(session, owner_id)
    return [
        {"id": a.id, "action": a.action, "target": a.target,
         "operator": str(a.owner_id), "at": _iso(a.created_at)}
        for a in rows
    ]


# ---------------- 阅读进度（M13.3） ----------------
async def get_progress(session, owner_id: int, novel_id: int):
    """取阅读进度。"""
    obj = await extension_repo.get_progress(session, owner_id, novel_id)
    if not obj:
        return None
    return {"novel_id": obj.novel_id, "chapter_id": obj.chapter_id, "position": obj.position}


async def save_progress(session, owner_id: int, novel_id: int, chapter_id: int | None, position: int):
    """写入阅读进度。"""
    obj = await extension_repo.upsert_progress(session, owner_id, novel_id, chapter_id, position)
    await session.commit()
    return {"novel_id": obj.novel_id, "chapter_id": obj.chapter_id, "position": obj.position}
