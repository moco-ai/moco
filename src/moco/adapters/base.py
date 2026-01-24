from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime
from abc import ABC, abstractmethod

@dataclass
class MediaAttachment:
    """メディア添付"""
    type: str                      # "image", "audio", "video", "file"
    url: str                       # ダウンロードURL
    mime_type: str
    size_bytes: int = 0
    filename: Optional[str] = None
    duration_seconds: Optional[float] = None  # audio/video用

@dataclass
class LocationData:
    """位置情報"""
    latitude: float
    longitude: float
    accuracy_meters: Optional[float] = None
    address: Optional[str] = None

@dataclass
class NormalizedMessage:
    """正規化されたメッセージ"""
    message_id: str
    channel_type: str              # "line", "telegram", "slack"
    sender_id: str                 # プラットフォーム側のユーザーID
    sender_name: str
    conversation_id: str           # DM or グループID
    is_group: bool
    
    # コンテンツ
    text: Optional[str] = None
    media: Optional[List[MediaAttachment]] = None
    location: Optional[LocationData] = None
    
    # メタデータ
    timestamp: datetime = None
    reply_to_id: Optional[str] = None
    raw_payload: dict = None

@dataclass
class OutgoingMessage:
    """送信メッセージ"""
    text: Optional[str] = None
    media_url: Optional[str] = None
    buttons: Optional[List[dict]] = None  # クイックリプライ等
    raw_payload: Optional[dict] = None     # アダプター固有の追加データ用

class ChannelAdapter(ABC):
    """Channelアダプター基底クラス"""
    
    @abstractmethod
    async def handle_webhook(self, request: dict) -> dict:
        """Webhook受信処理"""
        pass
    
    @abstractmethod
    async def send_message(self, conversation_id: str, message: OutgoingMessage) -> bool:
        """メッセージ送信"""
        pass
    
    @abstractmethod
    async def download_media(self, media: MediaAttachment) -> bytes:
        """メディアダウンロード"""
        pass
