"""
性能探针：测量各 LLM 相关接口的真实响应时间。

整体思路：
  直接调用 service 层（不走 HTTP），对每个耗时环节用 time.perf_counter 打点，
  输出逐接口耗时、首 token 延迟（stream）、以及整体耗时。
  用于定位「外部 API 响应慢」的瓶颈环节。

关键点：
  1. 不依赖正在运行的服务器；需要数据库中有对应小说/章节数据。
  2. 模型选择：通过 --model 指定；若库中存在多个 chat 配置，默认挑 is_default 的。
  3. 每个测试项独立计时，互不干扰；失败也记录耗时（便于发现「挂起到超时」的环节）。

用法：
  cd e:\AI_Pro\RAG_Pro\pythonProject\core
  python tools/perf_probe.py --novel-id 3 --user-id 6 --model gpt-5.4
"""
import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone

# 将项目根（core 的父目录）与 core 目录都加入 path，兼容 `from core.xxx` 与 `from repositories` 两种导入
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CORE_DIR = os.path.join(_PROJ_ROOT, "core")
for _p in (_PROJ_ROOT, _CORE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import DB_DSN
from core.repositories import llm_repo
from core.services import writing_service, drama_service


async def _with_session(fn, *args, **kw):
    engine = create_async_engine(DB_DSN, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        try:
            return await fn(session, *args, **kw)
        finally:
            await engine.dispose()


async def probe_dispatch(session, user_id, model):
    """测量模型分发 + 一次真实 chat 的 RTT。"""
    cfgs = await llm_repo.list_for_dispatch(session, user_id, "chat")
    if not cfgs:
        return {"接口": "模型分发", "结果": "无 chat 配置"}
    # 选中指定模型（按 model 名称模糊匹配）或默认首项（is_default 优先）
    cfg = None
    if model:
        cfg = next((c for c in cfgs if model.lower() in (c.model or "").lower()), None)
    if cfg is None:
        cfg = cfgs[0]
    from llm import langchain_factory as llm_adapters

    adapter = llm_adapters.get_adapter(cfg, None)
    messages = [{"role": "user", "content": "用一句话回复：你好"}]
    t0 = time.perf_counter()
    try:
        raw = await asyncio.wait_for(adapter.chat(messages), timeout=120)
        ok = "OK"
    except Exception as e:  # noqa
        raw = "ERROR: %s" % e
        ok = "ERR"
    t1 = time.perf_counter()
    return {
        "接口": "单次 chat RTT(%s)" % cfg.model,
        "模型": cfg.model,
        "base_url": cfg.base_url,
        "耗时(s)": round(t1 - t0, 2),
        "结果": ok,
        "输出前20字": (str(raw)[:20] if raw else ""),
    }


async def probe_continue(session, novel_id, user_id, model):
    """测量续写首 token 延迟（流式）。"""
    t0 = time.perf_counter()
    first_token = None
    chars = 0
    try:
        async for piece in writing_service.stream_continue(
            session, user_id, novel_id, {"prompt": "请继续写下一章。", "length": "mid"}
        ):
            if piece:
                chars += len(piece)
                if first_token is None:
                    first_token = time.perf_counter()
    except Exception as e:  # noqa
        pass
    t1 = time.perf_counter()
    ft = (first_token - t0) if first_token else None
    return {
        "接口": "续写 stream_continue(首token/总字数)",
        "首token延迟(s)": round(ft, 2) if ft else ">=%.1f" % (t1 - t0),
        "总耗时(s)": round(t1 - t0, 2),
        "收到字数": chars,
    }


async def probe_overview(session, novel_id, user_id):
    """测量概览类接口（summary / timeline / character_arc）耗时。"""
    out = []
    for fn_name, fn in [
        ("概览 generate_summary", writing_service.generate_summary),
        ("时间线 generate_timeline", writing_service.generate_timeline),
        ("角色弧线 generate_character_arc", writing_service.generate_character_arc),
    ]:
        t0 = time.perf_counter()
        try:
            await fn(session, user_id, novel_id)
            ok = "OK"
        except Exception as e:  # noqa
            ok = "ERR:%s" % e
        t1 = time.perf_counter()
        out.append({"接口": fn_name, "耗时(s)": round(t1 - t0, 2), "结果": ok})
    return out


async def probe_drama(session, novel_id, user_id):
    """测量章节漫剧导演 Agent 生成耗时，并统计四段式字数（验证「字数过少」修复）。"""
    dramas = await drama_service.list_dramas(session, novel_id)
    if not dramas:
        # 自动创建第 1 章漫剧用于测试（验证修复后字数是否充足）
        d = await drama_service.create_drama(
            session, novel_id, user_id,
            {"title": "性能探针测试漫剧-第1章", "chapter_from": 1, "chapter_to": 1}
        )
        drama_id = d["id"]
    else:
        drama_id = dramas[0]["id"]
    t0 = time.perf_counter()
    try:
        res = await drama_service.director_agent_generate(session, user_id, drama_id)
        ok = "OK" if res and res.get("scene_design") else "EMPTY"
    except Exception as e:  # noqa
        ok = "ERR:%s" % e
        res = None
    t1 = time.perf_counter()
    word_stats = {}
    if res:
        for k in ("scene_design", "plot_arrangement", "camera_movement", "duration_estimate"):
            v = res.get(k)
            if isinstance(v, str):
                word_stats["%s字数" % k] = len(v)
            elif isinstance(v, list):
                word_stats["%s条数" % k] = len(v)
    return [{
        "接口": "漫剧导演生成 director_agent_generate",
        "耗时(s)": round(t1 - t0, 2),
        "结果": ok,
        **word_stats,
    }]


async def probe_minio_save(session, novel_id, user_id):
    """测量新增的 MinIO 续写保存/读取功能。"""
    out = []
    name = "perf_probe_test_%s" % int(time.time())
    content = "性能探针测试内容：" + "续写保存功能验证。" * 20
    # 保存
    t0 = time.perf_counter()
    try:
        await writing_service.save_continue_write(session, user_id, novel_id, name, content)
        ok_save = "OK"
    except Exception as e:  # noqa
        ok_save = "ERR:%s" % e
    t1 = time.perf_counter()
    out.append({"接口": "MinIO save_continue_write", "耗时(s)": round(t1 - t0, 2), "结果": ok_save})
    # 列出
    t0 = time.perf_counter()
    try:
        lst = await writing_service.list_continue_writes(session, user_id, novel_id)
        ok_list = "OK(%d条)" % len(lst)
    except Exception as e:  # noqa
        lst, ok_list = [], "ERR:%s" % e
    t1 = time.perf_counter()
    out.append({"接口": "MinIO list_continue_writes", "耗时(s)": round(t1 - t0, 2), "结果": ok_list})
    # 读取刚刚保存的
    if isinstance(lst, list) and lst:
        target = next((x for x in lst if name in (x.get("name") or "")), lst[0])
        key = target.get("object_key") or target.get("key")
        t0 = time.perf_counter()
        try:
            read_back = await writing_service.read_continue_write(session, user_id, key)
            ok_read = "OK(读回%d字)" % len(read_back or "")
        except Exception as e:  # noqa
            ok_read = "ERR:%s" % e
        t1 = time.perf_counter()
        out.append({"接口": "MinIO read_continue_write", "耗时(s)": round(t1 - t0, 2), "结果": ok_read})
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--novel-id", type=int, required=True)
    ap.add_argument("--user-id", type=int, required=True)
    ap.add_argument("--model", type=str, default=None)
    args = ap.parse_args()

    results = []
    results.append(await _with_session(probe_dispatch, args.user_id, args.model))
    results.append(await _with_session(probe_continue, args.novel_id, args.user_id, args.model))
    results.extend(await _with_session(probe_overview, args.novel_id, args.user_id))
    results.extend(await _with_session(probe_drama, args.novel_id, args.user_id))
    results.extend(await _with_session(probe_minio_save, args.novel_id, args.user_id))

    print("\n===== 性能探针结果 @ %s =====" % datetime.now(timezone.utc).isoformat())
    print("模型筛选: %s" % (args.model or "(默认 is_default)"))
    for r in results:
        print(r)
    print("\n解读：若「单次 chat RTT」很高，说明瓶颈在外部 API 网络/模型本身；"
          "若「续写首token」远高于 RTT，说明服务端 prompt 构造/排队耗时大。")


if __name__ == "__main__":
    asyncio.run(main())
