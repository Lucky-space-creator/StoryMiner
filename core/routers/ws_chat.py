"""
对话 WebSocket 路由（M7 流式输出）

整体思路：
    提供 /ws/conversations/{conv_id} 长连接，客户端发送 {type:'message',content}，
    服务端经 RAG + 角色扮演构造 prompt 后调用 chat_stream 逐 token 回传
    {type:'token',content}，结束发 {type:'done'}，异常发 {type:'error',content}。

关键点：
    1. 鉴权从 query param token 取（WebSocket 无法带 Authorization 头），decode_token 校验。
    2. WS 不在 HTTP 请求上下文，需自建 SessionLocal 会话并在断开时关闭。
    3. 单连接循环收消息；每条消息独立触发一次流式生成并 commit 持久化。

实现逻辑：
    accept → 校验 token → 循环 receive_text → JSON 解析 → 调 service.stream_reply
    逐 token send_json → done/error 收尾；WebSocketDisconnect 优雅退出。
"""
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from db import SessionLocal
from auth.jwt import decode_token
from common.exceptions import BizError
from services import conversation_service

ws_router = APIRouter(tags=["M7对话WebSocket"])


@ws_router.websocket("/ws/conversations/{conv_id}")
async def chat_ws(websocket: WebSocket, conv_id: int, token: str = Query(...)):
    """对话流式 WebSocket 端点。"""
    await websocket.accept()
    # 鉴权：query param token
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
            if msg.get("type") != "message":
                continue
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            try:
                async for piece in conversation_service.stream_reply(
                    session, owner_id, conv_id, content
                ):
                    await websocket.send_json({"type": "token", "content": piece})
                await session.commit()
                await websocket.send_json({"type": "done"})
            except BizError as e:
                await websocket.send_json({"type": "error", "content": e.msg})
            except Exception as e:  # noqa: BLE001 兜底，避免连接整体崩溃
                await websocket.send_json({"type": "error", "content": str(e) or "对话生成失败"})
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
