import hmac
import hashlib
import base64
from typing import Any, Dict, Optional, List
import aiohttp
import logging
from datetime import datetime
from .base import ChannelAdapter, NormalizedMessage, MediaAttachment, OutgoingMessage

logger = logging.getLogger(__name__)

class LINEAdapter(ChannelAdapter):
    """LINE Messaging API 用のアダプター"""
    
    API_BASE = "https://api.line.me/v2/bot"
    DATA_BASE = "https://api-data.line.me/v2/bot"
    
    def __init__(self, channel_access_token: str, channel_secret: Optional[str] = None):
        self.channel_access_token = channel_access_token
        self.channel_secret = channel_secret
        self.headers = {
            "Authorization": f"Bearer {self.channel_access_token}",
            "Content-Type": "application/json"
        }
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def verify_signature(self, body: str, signature: str) -> bool:
        """Webhookの署名を検証"""
        if not self.channel_secret:
            logger.warning("LINE_CHANNEL_SECRET not set. Skipping signature verification.")
            return True # 開発用。本番ではFalseにすべき
            
        hash = hmac.new(
            self.channel_secret.encode('utf-8'),
            body.encode('utf-8'),
            hashlib.sha256
        ).digest()
        expected_signature = base64.b64encode(hash).decode('utf-8')
        return hmac.compare_digest(expected_signature, signature)

    async def handle_webhook(self, request_body: dict) -> List[NormalizedMessage]:
        # 注意: 署名検証はFastAPIのルート側で行うことを推奨するが、
        # ここではペイロードのパースのみを行う。
        # (実際の実装では api.py で verify_signature を呼ぶ必要がある)
        events = request_body.get("events", [])
        normalized_messages = []
        
        for event in events:
            if event.get("type") != "message":
                continue
                
            msg = event.get("message", {})
            source = event.get("source", {})
            
            # メッセージ種別の判別
            msg_type = msg.get("type")
            text = msg.get("text") if msg_type == "text" else None
            
            # メディアの抽出
            media = []
            if msg_type in ("image", "audio", "video", "file"):
                media.append(MediaAttachment(
                    type=msg_type,
                    url=f"{self.DATA_BASE}/message/{msg.get('id')}/content",
                    mime_type="application/octet-stream", # あとでダウンロード時に判別
                    filename=msg.get("fileName")
                ))
            
            # 誰から・どこから
            sender_id = source.get("userId")
            group_id = source.get("groupId") or source.get("roomId")
            is_group = group_id is not None
            
            normalized = NormalizedMessage(
                message_id=msg.get("id"),
                channel_type="line",
                sender_id=sender_id,
                sender_name="LINE User", # プロフィール取得が必要なら別途実装
                conversation_id=group_id if is_group else sender_id,
                is_group=is_group,
                text=text,
                media=media if media else None,
                timestamp=datetime.fromtimestamp(event.get("timestamp") / 1000.0),
                raw_payload=event
            )
            normalized_messages.append(normalized)
            
        return normalized_messages

    async def send_message(self, conversation_id: str, message: OutgoingMessage) -> bool:
        """LINE にメッセージを送信"""
        url = f"{self.API_BASE}/message/push"
        
        messages = []
        
        # 承認リクエスト用の Flex Message 判定
        if message.text and "approval_id" in (message.raw_payload or {}):
            # Section 6.2 の Flex Message 形式
            approval_id = message.raw_payload["approval_id"]
            tool_name = message.raw_payload.get("tool", "Tool")
            tool_args = message.raw_payload.get("args", {})
            
            messages.append({
                "type": "flex",
                "altText": f"承認リクエスト: {tool_name}",
                "contents": {
                    "type": "bubble",
                    "header": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [{"type": "text", "text": "承認リクエスト", "weight": "bold", "size": "lg"}]
                    },
                    "body": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {"type": "text", "text": tool_name, "weight": "bold"},
                            {"type": "text", "text": str(tool_args)[:100], "size": "sm", "color": "#666666", "wrap": True}
                        ]
                    },
                    "footer": {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "button",
                                "action": {"type": "postback", "label": "承認", "data": f"approve:{approval_id}"},
                                "style": "primary"
                            },
                            {
                                "type": "button",
                                "action": {"type": "postback", "label": "拒否", "data": f"reject:{approval_id}"},
                                "style": "secondary"
                            }
                        ]
                    }
                }
            })
        elif message.text:
            messages.append({"type": "text", "text": message.text})
            
        if message.media_url:
            # 簡単のため画像と仮定
            messages.append({
                "type": "image",
                "originalContentUrl": message.media_url,
                "previewImageUrl": message.media_url
            })
            
        if not messages:
            return False
            
        payload = {
            "to": conversation_id,
            "messages": messages
        }
        
        session = await self._get_session()
        async with session.post(url, headers=self.headers, json=payload) as resp:
            if resp.status == 200:
                return True
            else:
                error_text = await resp.text()
                logger.error(f"LINE send_message failed: {resp.status} - {error_text}")
                return False

    async def download_media(self, media: MediaAttachment) -> bytes:
        """LINE からメディアコンテンツをダウンロード"""
        headers = {"Authorization": f"Bearer {self.channel_access_token}"}
        session = await self._get_session()
        async with session.get(media.url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.read()
            else:
                error_text = await resp.text()
                logger.error(f"LINE download_media failed: {resp.status} - {error_text}")
                return b""
