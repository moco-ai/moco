import secrets
import json
import logging
import os
import uuid
from typing import Any, Dict, Optional
from fastapi import WebSocket, WebSocketDisconnect
from moco.ui.api import approval_manager

logger = logging.getLogger(__name__)

# Gateway用共有シークレット（環境変数から取得）
GATEWAY_SECRET = os.getenv("MOCO_GATEWAY_SECRET")
if not GATEWAY_SECRET:
    # 開発環境での利便性を考慮しつつ、警告を出すかエラーにする
    # 本番運用を想定し、デフォルト値は廃止
    logger.error("MOCO_GATEWAY_SECRET is not set. Gateway connections will fail.")

async def handle_gateway_connection(websocket: WebSocket, token: Optional[str] = None):
    """
    Mobile Gatewayクライアント（LineAdapter等）からのWebSocket接続を処理する。
    """
    if not GATEWAY_SECRET:
        await websocket.accept()
        await websocket.send_json({"type": "error", "message": "Server misconfiguration: Secret not set"})
        await websocket.close(code=4003)
        return

    await websocket.accept()
    
    try:
        # 1. 認証フェーズ（クエリパラメータまたは初期メッセージ）
        client_token = token
        if not client_token:
            auth_payload = await websocket.receive_json()
            if auth_payload.get("type") == "auth":
                client_token = auth_payload.get("token")
        
        if not client_token or not secrets.compare_digest(client_token, GATEWAY_SECRET):
            logger.warning("Gateway authentication failed")
            await websocket.send_json({"type": "error", "message": "Invalid token"})
            await websocket.close(code=4003)
            return

        logger.info("Gateway client authenticated")
        await websocket.send_json({"type": "auth_ok"})
        
        # 2. 登録
        client_id = str(uuid.uuid4())
        await approval_manager.register_gateway_client(client_id, websocket)
        
        # 3. メッセージループ
        try:
            while True:
                data = await websocket.receive_json()
                
                # 基盤側への応答を処理（承認決定など）
                # 仕様書に基づき "approval.response" も考慮
                msg_type = data.get("type")
                if msg_type in ["approval_response", "approval.response"]:
                    approval_id = data.get("approval_id")
                    approved = data.get("approved")
                    if approval_id:
                        await approval_manager.respond_to_approval(approval_id, approved)
                
                # 必要に応じて他のメッセージタイプも拡張可能
                
        except WebSocketDisconnect:
            logger.info("Gateway client disconnected")
        finally:
            await approval_manager.unregister_gateway_client(client_id)
            
    except Exception as e:
        logger.error(f"Error in Gateway connection: {e}")
        try:
            await websocket.close()
        except:
            pass
