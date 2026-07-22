"""
阅读对话 WebSocket 路由（M3）

整体思路：
    单端点 /ws/novels/{novel_id}/reading-chat，按 token 鉴权后，从浏览器收一条 JSON
    {content, attachments, llm_config_id}，调用 reading_chat_service.send_message 流式生成，
    再把 delta/done/error 帧回推；关闭时清理连接。

关键点：
    1. 鉴权：token 取自 query 参数 ?token=，失败立即关闭并返回 4401。
    2. 消息协议：入参与回参均为 JSON 文本帧，便于前端统一解析。
    3. 异常兜底：任何未捕获异常都转成 error 帧，避免 WebSocket 静默断开。

实现逻辑：
    websocket 端点 accept -> 校验 token -> 循环 receive_text -> 解析 -> 调服务生成器 -> 逐帧 send_json。
"""
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from auth.jwt import decode_token
from common.exceptions import BizError
from services.reading_chat_service import send_message


ws_router = APIRouter(tags=["M3阅读对话WebSocket"])


@ws_router.websocket("/ws/novels/{novel_id}/reading-chat")
async def reading_chat_ws(
    websocket: WebSocket, novel_id: int, token: str = Query(...)
):
    """阅读对话流式 WebSocket 端点。"""
    await websocket.accept()
    try:
        owner_id = decode_token(token)
    except BizError as e:
        await websocket.send_json({"type": "error", "message": e.msg})
        await websocket.close()
        return

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                await websocket.send_json({"type": "error", "message": "消息格式错误（需 JSON）"})
                continue

            content = (payload.get("content") or "").strip()
            if not content:
                await websocket.send_json({"type": "error", "message": "消息内容不能为空"})
                continue

            attachments = payload.get("attachments") or None
            llm_config_id = payload.get("llm_config_id")
            async for frame in send_message(
                user_id=owner_id,
                novel_id=novel_id,
                content=content,
                attachments=attachments,
                llm_config_id=llm_config_id,
            ):
                await websocket.send_json(frame)
    except WebSocketDisconnect:
        return
    except Exception as e:  # noqa: BLE001 兜底，避免连接整体崩溃
        try:
            await websocket.send_json({"type": "error", "message": f"服务异常：{e}"})
        except Exception:
            pass
