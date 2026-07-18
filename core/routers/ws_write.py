"""
续写 WebSocket 路由（M8.4/M8.5 流式续写）

整体思路：
    提供 /ws/novels/{novel_id}/continue-write 长连接，客户端发送
    {type:'continue', chapter_id?, prompt, style, length, perspective}，
    服务端取前文并构造续写 prompt 后调用 chat_stream 逐 token 回传
    {type:'token',content}，结束自动保存版本并发送 {type:'done',version_id}，异常发 {type:'error',content}。

关键点：
    1. 鉴权从 query param token 取（WebSocket 无法带 Authorization 头），decode_token 校验。
    2. WS 不在 HTTP 请求上下文，需自建 SessionLocal 会话并在断开时关闭。
    3. 单连接循环收消息；每条续写独立触发一次流式生成，累计完整正文后持久化版本。

实现逻辑：
    accept → 校验 token → 循环 receive_text → JSON 解析 → 调 service.stream_continue
    逐 token send_json → 累计 full → save_version → done/error 收尾；WebSocketDisconnect 优雅退出。
"""
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from db import SessionLocal
from auth.jwt import decode_token
from common.exceptions import BizError
from services import writing_service

ws_router = APIRouter(tags=["M8续写WebSocket"])


@ws_router.websocket("/ws/novels/{novel_id}/continue-write")
async def write_ws(websocket: WebSocket, novel_id: int, token: str = Query(...)):
    """续写流式 WebSocket 端点。"""
    await websocket.accept()
    try:
        owner_id = decode_token(token)
    except BizError as e:
        await websocket.send_json({"type": "error", "content": e.msg})
        await websocket.close()
        return

    session = SessionLocal()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                await websocket.send_json({"type": "error", "content": "消息格式错误（需 JSON）"})
                continue
            if msg.get("type") != "continue":
                continue
            payload = {
                "chapter_id": msg.get("chapter_id"),
                "prompt": msg.get("prompt"),
                "style": msg.get("style", "original"),
                "length": msg.get("length", "mid"),
                "perspective": msg.get("perspective", "third"),
                "title": msg.get("title"),
            }
            try:
                full = ""
                async for piece in writing_service.stream_continue(
                    session, owner_id, novel_id, payload
                ):
                    full += piece
                    await websocket.send_json({"type": "token", "content": piece})
                version = await writing_service.save_version(
                    session, owner_id, novel_id, payload, full
                )
                await session.commit()
                await websocket.send_json({"type": "done", "version_id": version.id})
            except BizError as e:
                await websocket.send_json({"type": "error", "content": e.msg})
            except Exception:  # noqa: BLE001 兜底，避免连接整体崩溃；不向客户端泄露内部异常细节
                await websocket.send_json({"type": "error", "content": "续写生成失败，请稍后重试"})
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
