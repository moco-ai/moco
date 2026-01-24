import os
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime
import aiohttp
from .base import ChannelAdapter, NormalizedMessage, MediaAttachment, OutgoingMessage, LocationData

logger = logging.getLogger(__name__)

class TelegramAdapter(ChannelAdapter):
    """Telegram Bot API 用のアダプター"""
    
    API_BASE = "https://api.telegram.org/bot"
    
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.api_url = f"{self.API_BASE}{bot_token}"
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def handle_webhook(self, request_body: dict) -> List[NormalizedMessage]:
        """Telegram Webhook ペイロードを正規化"""
        # Telegram sends one update per webhook call
        update = request_body
        normalized_messages = []
        
        message = update.get("message")
        if not message:
            return []
            
        chat = message.get("chat", {})
        sender = message.get("from", {})
        
        message_id = str(message.get("message_id"))
        chat_id = str(chat.get("id"))
        sender_id = str(sender.get("id"))
        sender_name = sender.get("first_name", "")
        if sender.get("last_name"):
            sender_name += f" {sender['last_name']}"
            
        is_group = chat.get("type") in ("group", "supergroup", "channel")
        
        text = message.get("text")
        media = []
        location = None
        
        # 写真 (最も解像度が高いものを選択)
        if "photo" in message:
            photo = message["photo"][-1]
            file_id = photo["file_id"]
            media.append(MediaAttachment(
                type="image",
                url=f"tgfile://{file_id}", # 遅延取得のため prefix を付与
                mime_type="image/jpeg",
                size_bytes=photo.get("file_size", 0)
            ))
            
        # 音声
        if "voice" in message:
            voice = message["voice"]
            media.append(MediaAttachment(
                type="audio",
                url=f"tgfile://{voice['file_id']}", # 遅延取得のため prefix を付与
                mime_type=voice.get("mime_type", "audio/ogg"),
                size_bytes=voice.get("file_size", 0),
                duration_seconds=voice.get("duration")
            ))
            
        # 位置情報
        if "location" in message:
            loc = message["location"]
            location = LocationData(
                latitude=loc["latitude"],
                longitude=loc["longitude"]
            )
            
        normalized = NormalizedMessage(
            message_id=message_id,
            channel_type="telegram",
            sender_id=sender_id,
            sender_name=sender_name,
            conversation_id=chat_id,
            is_group=is_group,
            text=text,
            media=media if media else None,
            location=location,
            timestamp=datetime.fromtimestamp(message.get("date", datetime.now().timestamp())),
            raw_payload=update
        )
        normalized_messages.append(normalized)
        
        return normalized_messages

    async def send_message(self, conversation_id: str, message: OutgoingMessage) -> bool:
        """Telegram にメッセージを送信"""
        session = await self._get_session()
        # テキスト送信
        if message.text:
            url = f"{self.api_url}/sendMessage"
            payload = {
                "chat_id": conversation_id,
                "text": message.text
            }
            # シンプルなボタン（InlineKeyboardButton）があれば追加
            if message.buttons:
                keyboard = []
                for btn in message.buttons:
                    keyboard.append([{
                        "text": btn.get("label", "Button"),
                        "callback_data": btn.get("data", "none")
                    }])
                payload["reply_markup"] = {"inline_keyboard": keyboard}
            
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    logger.error(f"Telegram sendMessage failed: {resp.status} - {await resp.text()}")
                    return False
        
        # メディア送信
        if message.media_url:
            url = f"{self.api_url}/sendPhoto" # 簡略化のため画像固定
            payload = {
                "chat_id": conversation_id,
                "photo": message.media_url
            }
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    logger.error(f"Telegram sendPhoto failed: {resp.status} - {await resp.text()}")
                    return False
                        
        return True

    async def download_media(self, media: MediaAttachment) -> bytes:
        """Telegram からメディアをダウンロード"""
        url = media.url
        if not url:
            return b""
        
        # 遅延取得のスキームを確認
        if url.startswith("tgfile://"):
            file_id = url.replace("tgfile://", "")
            url = await self._get_file_url(file_id)
            if not url:
                return b""

        session = await self._get_session()
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.read()
            return b""

    async def _get_file_url(self, file_id: str) -> str:
        """file_id から直リンクURLを取得"""
        url = f"{self.api_url}/getFile"
        session = await self._get_session()
        async with session.post(url, json={"file_id": file_id}) as resp:
            if resp.status == 200:
                data = await resp.json()
                file_path = data.get("result", {}).get("file_path")
                if file_path:
                    return f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
        return ""
