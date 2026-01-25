from typing import Any, Dict, Optional
import logging
import asyncio

logger = logging.getLogger(__name__)

class NotifyMobileTool:
    """
    モバイル端末（Gateway経由）に通知を送信するツール。
    """
    
    def __init__(self, approval_manager=None):
        # 実行時に ApprovalManager が渡されることを想定
        self.approval_manager = approval_manager

    def run(self, message: str, title: Optional[str] = "Moco Notification", level: str = "info") -> str:
        """
        モバイルクライアントに通知を送信します。
        
        Args:
            message: 通知本文
            title: 通知タイトル
            level: 通知レベル (info, warning, error)
        """
        if not self.approval_manager:
            # シングルトン的な取得を試みる
            try:
                from moco.ui.api import approval_manager
                self.approval_manager = approval_manager
            except ImportError:
                return "Error: ApprovalManager not found. Cannot send notification."

        payload = {
            "type": "notification",
            "title": title,
            "message": message,
            "level": level
        }

        # 非同期ループ内での呼び出しを想定
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._broadcast(payload),
                    loop
                )
            else:
                loop.run_until_complete(self._broadcast(payload))
        except Exception as e:
            # loopがない場合やスレッドが異なる場合、同期的に送れない可能性あり
            # ApprovalManager._send_to_session は内部で await を使っているため、
            # 常に非同期で実行する必要がある
            logger.error(f"Failed to broadcast notification: {e}")
            return f"Error sending notification: {e}"

        return f"Notification sent to {len(self.approval_manager.gateway_clients)} clients."

    async def _broadcast(self, payload: Dict[str, Any]):
        clients = list(self.approval_manager.gateway_clients.values())
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception as e:
                logger.warning(f"Failed to send to a gateway client: {e}")

class RequestLocationTool:
    """
    モバイル端末に位置情報をリクエストするツール。
    """
    def __init__(self, approval_manager=None):
        self.approval_manager = approval_manager

    def run(self, client_id: Optional[str] = None) -> str:
        """
        位置情報をリクエストします。現在はタイムアウト返却となります。
        """
        if not self.approval_manager:
            try:
                from moco.ui.api import approval_manager
                self.approval_manager = approval_manager
            except ImportError:
                return "Error: ApprovalManager not found."

        # WebSocketにリクエスト送信
        payload = {"type": "location.request"}
        
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(self._request(payload, client_id), loop)
        else:
            loop.run_until_complete(self._request(payload, client_id))

        # モバイルからの非同期返信を待つ仕組みは今回省略し、仕様に従いタイムアウト表示とする
        return "[Location access timed out or pending user action on mobile device]"

    async def _request(self, payload: dict, client_id: Optional[str]):
        clients = []
        if client_id and client_id in self.approval_manager.gateway_clients:
            clients = [self.approval_manager.gateway_clients[client_id]]
        else:
            clients = list(self.approval_manager.gateway_clients.values())

        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception:
                continue
