"""
Mobile client tools - ファイルをモバイルクライアント（WhatsApp/iMessage等）に送信するツール
"""
from typing import Any, Dict, Optional, List
import logging
import asyncio
import threading
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# セッションごとのアーティファクトストレージ（ロック付き）
# スレッド間で共有されるため、threading.local()ではなくグローバル変数を使用
_artifacts_lock = threading.Lock()
_pending_artifacts: Dict[str, List[Dict[str, Any]]] = {}  # {session_id: [artifacts]}

# 現在のセッションID（スレッドローカル）
_current_session = threading.local()


def set_current_session(session_id: str) -> None:
    """現在のセッションIDを設定"""
    _current_session.session_id = session_id


def get_current_session() -> Optional[str]:
    """現在のセッションIDを取得"""
    return getattr(_current_session, 'session_id', None)


def _clear_artifacts(session_id: Optional[str] = None) -> None:
    """アーティファクトリストをクリア（リクエスト開始時に呼ぶ）"""
    sid = session_id or get_current_session()
    if not sid:
        return
    with _artifacts_lock:
        _pending_artifacts[sid] = []


def _get_pending_artifacts(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """送信待ちのアーティファクトを取得してクリア"""
    sid = session_id or get_current_session()
    if not sid:
        return []
    with _artifacts_lock:
        artifacts = _pending_artifacts.get(sid, []).copy()
        _pending_artifacts[sid] = []
    return artifacts


def _add_artifact(artifact: Dict[str, Any], session_id: Optional[str] = None) -> None:
    """アーティファクトを追加"""
    sid = session_id or get_current_session()
    if not sid:
        # セッションIDがない場合は警告だけ出して追加しない
        logger.warning("No session ID set for artifact, skipping")
        return
    with _artifacts_lock:
        if sid not in _pending_artifacts:
            _pending_artifacts[sid] = []
        _pending_artifacts[sid].append(artifact)


# 外部からアクセスするための公開エイリアス
clear_artifacts = _clear_artifacts
get_pending_artifacts = _get_pending_artifacts


def _detect_file_type(file_path: str) -> str:
    """ファイルの拡張子からタイプを判定"""
    ext = Path(file_path).suffix.lower()
    
    image_exts = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff'}
    video_exts = {'.mp4', '.mov', '.avi', '.mkv', '.webm'}
    audio_exts = {'.mp3', '.wav', '.ogg', '.m4a', '.flac'}
    
    if ext in image_exts:
        return "image"
    elif ext in video_exts:
        return "video"
    elif ext in audio_exts:
        return "audio"
    else:
        return "document"


try:
    from ..utils.path import resolve_safe_path
except ImportError:
    try:
        from moco.utils.path import resolve_safe_path
    except ImportError:
        def resolve_safe_path(p): return os.path.abspath(p)


def send_file_to_mobile(
    file_path: str,
    caption: str = "",
    file_type: str = "auto"
) -> str:
    """
    ファイルをモバイルクライアント（WhatsApp/iMessage等）に送信する。
    
    このツールを使うと、指定したファイルがモバイルクライアントに送信されます。
    画像生成後に結果を送信したい場合や、ドキュメントを共有したい場合に使用します。
    
    Args:
        file_path: 送信するファイルのパス（絶対パスまたは作業ディレクトリからの相対パス）
        caption: ファイルに添付するキャプション（省略可）
        file_type: ファイルタイプ。"image", "document", "video", "audio", "auto"
                   "auto"を指定すると拡張子から自動判定
    
    Returns:
        送信結果のメッセージ
    
    Examples:
        # 画像を送信
        send_file_to_mobile("generated_images/cat.png", "生成した猫の画像です")
        
        # ドキュメントを送信
        send_file_to_mobile("reports/analysis.pdf", "分析レポート")
        
        # 自動判定で送信
        send_file_to_mobile("output/result.png")
    """
    # パスの解決
    try:
        path = Path(resolve_safe_path(file_path))
    except Exception:
        path = Path(file_path)
        if not path.is_absolute():
            working_dir = os.getenv("MOCO_WORKING_DIRECTORY") or os.getcwd()
            path = Path(working_dir) / file_path
    
    # ファイル存在確認
    if not path.exists():
        return f"Error: File not found: {file_path}"
    
    if not path.is_file():
        return f"Error: Path is not a file: {file_path}"
    
    # ファイルタイプの判定
    if file_type == "auto":
        detected_type = _detect_file_type(str(path))
    else:
        detected_type = file_type
    
    # アーティファクトとして登録（スレッドセーフ）
    artifact = {
        "type": detected_type,
        "path": str(path.absolute()),
        "caption": caption,
        "filename": path.name
    }
    _add_artifact(artifact)
    
    logger.info(f"Artifact added: {artifact}")
    
    # 結果メッセージ
    type_names = {
        "image": "画像",
        "document": "ドキュメント",
        "video": "動画",
        "audio": "音声"
    }
    type_name = type_names.get(detected_type, "ファイル")
    
    return f"✅ {type_name}をモバイルクライアントへの送信キューに追加しました: {path.name}"


# Legacy classes (kept for backward compatibility)
class NotifyMobileTool:
    """
    モバイル端末（Gateway経由）に通知を送信するツール。
    """
    
    def __init__(self, approval_manager=None):
        self.approval_manager = approval_manager

    def run(self, message: str, title: Optional[str] = "Moco Notification", level: str = "info") -> str:
        """
        モバイルクライアントに通知を送信します。
        """
        if not self.approval_manager:
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
        位置情報をリクエストします。
        """
        if not self.approval_manager:
            try:
                from moco.ui.api import approval_manager
                self.approval_manager = approval_manager
            except ImportError:
                return "Error: ApprovalManager not found."

        payload = {"type": "location.request"}
        
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(self._request(payload, client_id), loop)
        else:
            loop.run_until_complete(self._request(payload, client_id))

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
