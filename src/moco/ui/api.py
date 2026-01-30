"""
FastAPI backend for Moco Web UI
ChatGPT-like interface
"""
# ruff: noqa: E402
import os
import secrets
import re
import asyncio
import base64
import queue
import tempfile
import threading
import time
import uuid
from typing import Optional, Any, Dict, List, Tuple
from fastapi import FastAPI, HTTPException, WebSocket, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import json
import sqlite3
import logging
from dotenv import load_dotenv, find_dotenv

# .env を読み込む（親方向に自動探索）
load_dotenv(find_dotenv())

# moco imports
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from moco.core.orchestrator import Orchestrator
from moco.storage.session_logger import SessionLogger
from moco.tools.discovery import _find_profiles_dir
from moco.cancellation import (
    create_cancel_event,
    request_cancel,
    clear_cancel_event,
    OperationCancelled
)
from moco.tools.mobile import get_pending_artifacts, clear_artifacts
from moco.gateway.media_processor import MediaProcessor
from moco.utils.tunnel import setup_tunnel, stop_tunnel
from moco.adapters.line_adapter import LINEAdapter
from moco.adapters.telegram_adapter import TelegramAdapter
from moco.adapters.base import OutgoingMessage, ChannelAdapter


def filter_response_for_display(response: str, verbose: bool = False) -> str:
    """レスポンスをフィルタリング（verboseでない場合は最後のエージェントだけ）"""
    if not response:
        return ""
    if verbose:
        return response

    # @agent: 応答 のパターンで分割
    sections = re.split(r'(@[\w-]+):\s*', response)

    if len(sections) > 1:
        # 最後のエージェントの結果だけを取得
        last_agent = sections[-2] if len(sections) >= 2 else ""
        last_content = sections[-1].strip() if sections[-1] else ""

        # orchestrator の最終回答は省略しない
        if last_agent == "@orchestrator":
            return last_content
        else:
            # 中間エージェントの場合も全文返す（UIでの表示は別途対応）
            return f"{last_agent}: {last_content}"

    return response

app = FastAPI(title="Moco", version="1.0.0")

@app.on_event("startup")
async def startup_event():
    """起動時にトンネルをセットアップ"""
    port = int(os.getenv("PORT", 8000))
    # トンネルのセットアップ（失敗しても本体の起動を妨げない）
    try:
        setup_tunnel(port)
    except Exception as e:
        logger.error(f"Error during tunnel setup: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """終了時にトンネルを停止"""
    try:
        stop_tunnel()
    except Exception as e:
        logger.error(f"Error during tunnel shutdown: {e}")

# 静的ファイルのマウント
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# グローバル状態
session_logger = SessionLogger()
logger = logging.getLogger(__name__)

# ===== Approval (tool execution gating) =====
# Used by UI and by `src/moco/ui/test_approval.py`.
pending_approvals: Dict[str, Dict[str, Any]] = {}
pending_approvals_lock = threading.Lock()


class ApprovalManager:
    def __init__(self):
        self.pending_approvals = pending_approvals
        self.pending_approvals_lock = pending_approvals_lock
        self.session_websockets: Dict[str, list[Any]] = {}
        self.gateway_clients: Dict[str, Any] = {}
        
        # 外部通知用アダプターの管理
        self.adapters: List[ChannelAdapter] = []
        
        line_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
        line_secret = os.getenv("LINE_CHANNEL_SECRET")
        if line_token:
            self.adapters.append(LINEAdapter(line_token, line_secret))
            
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if tg_token:
            self.adapters.append(TelegramAdapter(tg_token))

    async def register_websocket(self, session_id: str, websocket: Any) -> None:
        self.session_websockets.setdefault(session_id, []).append(websocket)

    async def unregister_websocket(self, session_id: str, websocket: Any) -> None:
        ws_list = self.session_websockets.get(session_id) or []
        try:
            ws_list.remove(websocket)
        except ValueError:
            return
        if not ws_list:
            self.session_websockets.pop(session_id, None)

    async def register_gateway_client(self, client_id: str, websocket: Any) -> None:
        self.gateway_clients[client_id] = websocket

    async def unregister_gateway_client(self, client_id: str) -> None:
        self.gateway_clients.pop(client_id, None)

    async def _send_to_session(self, session_id: str, payload: Dict[str, Any]) -> bool:
        ws_list = self.session_websockets.get(session_id) or []
        # UI WebSockets
        sent = False
        for ws in list(ws_list):
            try:
                if hasattr(ws, "send_json"):
                    await ws.send_json(payload)
                    sent = True
                elif hasattr(ws, "send_text"):
                    import json
                    await ws.send_text(json.dumps(payload, ensure_ascii=False))
                    sent = True
            except Exception:
                continue
        return sent

    async def _notify_gateway_clients(self, approval_id: str, tool: str, args: dict, session_id: str):
        """Gateway経由でモバイルに通知"""
        message = {
            "type": "approval.request",
            "id": approval_id,
            "payload": {
                "approval_id": approval_id,
                "tool": tool,
                "args": args,
                "session_id": session_id
            }
        }
        
        # WebSocket クライアントへ送信
        clients = list(self.gateway_clients.items())
        for client_id, ws in clients:
            try:
                await ws.send_json(message)
            except Exception:
                self.gateway_clients.pop(client_id, None)

        # 外部チャネル（LINE/Telegram）へプッシュ送信
        await self._push_external_notifications(approval_id, tool, args)

    async def send_to_gateway(self, message: Dict[str, Any]) -> int:
        """
        Send a generic message to all connected gateway clients (WhatsApp, etc.).
        
        Args:
            message: Dictionary containing the message payload
            
        Returns:
            int: Number of clients that received the message
        """
        sent_count = 0
        clients = list(self.gateway_clients.items())
        for client_id, ws in clients:
            try:
                await ws.send_json(message)
                sent_count += 1
            except Exception as e:
                logger.warning(f"Failed to send to gateway client {client_id}: {e}")
                self.gateway_clients.pop(client_id, None)
        return sent_count


    async def _push_external_notifications(self, approval_id: str, tool: str, args: dict):
        """LINE/Telegramにプッシュ通知を送信"""
        text = f"承認リクエスト: {tool}\n引数: {json.dumps(args, ensure_ascii=False, indent=2)}"
        raw_payload = {
            "approval_id": approval_id,
            "tool": tool,
            "args": args
        }
        msg = OutgoingMessage(text=text, raw_payload=raw_payload)

        # 全てのアダプターに対して送信を試みる
        # LINE_USER_ID や TELEGRAM_CHAT_ID は環境変数から取得（将来的にDB管理が望ましい）
        targets = {
            "line": os.getenv("LINE_USER_ID"),
            "telegram": os.getenv("TELEGRAM_CHAT_ID")
        }

        for adapter in self.adapters:
            channel = "line" if isinstance(adapter, LINEAdapter) else "telegram"
            target_id = targets.get(channel)
            if target_id:
                try:
                    await adapter.send_message(target_id, msg)
                    logger.info(f"Sent approval request to {channel}: {approval_id}")
                except Exception as e:
                    logger.error(f"Failed to send {channel} notification: {e}")

    async def create_approval_request(self, approval_id: str, tool: str, args: Dict[str, Any], session_id: str) -> bool:
        with self.pending_approvals_lock:
            event = threading.Event()
            self.pending_approvals[approval_id] = {
                "event": event,
                "decision": False,
                "tool": tool,
                "args": args or {},
                "session_id": session_id,
            }

        # Web UIへ通知
        await self._send_to_session(
            session_id,
            {
                "type": "approval_request",
                "approval_id": approval_id,
                "tool": tool,
                "args": args or {},
                "session_id": session_id
            },
        )
        
        # Gateway接続クライアントへ通知
        await self._notify_gateway_clients(approval_id, tool, args, session_id)
        
        return True

    async def respond_to_approval(self, approval_id: str, approved: bool) -> bool:
        with self.pending_approvals_lock:
            item = self.pending_approvals.get(approval_id)
            if not item:
                return False
            item["decision"] = bool(approved)
            event = item.get("event")
            if isinstance(event, threading.Event):
                event.set()
        return True

    async def wait_for_decision(self, approval_id: str, timeout: float = 30.0) -> bool:
        with self.pending_approvals_lock:
            item = self.pending_approvals.get(approval_id)
            if not item:
                return False
            event = item.get("event")
            if not isinstance(event, threading.Event):
                return False

        decided = await asyncio.to_thread(event.wait, timeout)
        with self.pending_approvals_lock:
            item = self.pending_approvals.pop(approval_id, None)
        if not decided or not item:
            return False
        return bool(item.get("decision"))


approval_manager = ApprovalManager()


def get_orchestrator(profile: str, provider: str = "openrouter", verbose: bool = False, working_directory: str = None) -> Orchestrator:
    """Orchestratorインスタンスを新規生成"""
    # 作業ディレクトリ: 引数 > 環境変数 > カレントディレクトリ
    work_dir = working_directory or os.getenv("MOCO_WORKING_DIRECTORY") or os.getcwd()
    return Orchestrator(
        profile=profile,
        provider=provider,
        session_logger=session_logger,
        verbose=verbose,
        working_directory=work_dir
    )


# 一時ファイルを管理するためのディレクトリ
_TEMP_ATTACHMENTS_DIR = os.path.join(tempfile.gettempdir(), "moco_attachments")
os.makedirs(_TEMP_ATTACHMENTS_DIR, exist_ok=True)


def process_attachments(attachments: Optional[List["Attachment"]], message: str) -> Tuple[str, List[str]]:
    """
    添付ファイルを処理し、メッセージを拡張する。
    
    画像ファイルは一時ファイルに保存し、パスをメッセージに追加。
    テキストファイルはインラインで展開。
    
    Returns:
        Tuple[str, List[str]]: (拡張されたメッセージ, 作成した一時ファイルのパスリスト)
    """
    if not attachments:
        return message, []
    
    temp_files = []
    attachment_info = []
    
    for att in attachments:
        try:
            # pathがあればそのまま使用（WhatsApp経由など）
            if att.path:
                file_path = att.path
            # dataがあればデコードして保存（Web UI経由）
            elif att.data:
                data = base64.b64decode(att.data)
                ext = att.mime_type.split("/")[-1] if att.mime_type else "bin"
                if ext == "jpeg":
                    ext = "jpg"
                file_path = os.path.join(
                    _TEMP_ATTACHMENTS_DIR,
                    f"{uuid.uuid4().hex[:8]}_{att.name}"
                )
                with open(file_path, "wb") as f:
                    f.write(data)
                temp_files.append(file_path)
            else:
                attachment_info.append(f"[Invalid attachment: {att.name}]")
                continue
            
            # LLMにパスを渡す
            if att.type == "image":
                attachment_info.append(f"[Image: {att.name}] Path: {file_path}")
            else:
                attachment_info.append(f"[File: {att.name}] Path: {file_path}")
                
        except Exception as e:
            logger.warning(f"Failed to process attachment {att.name}: {e}")
            attachment_info.append(f"[Error processing {att.name}: {e}]")
    
    # メッセージに添付情報を追加
    if attachment_info:
        expanded_message = message
        if message:
            expanded_message += "\n\n"
        expanded_message += "## Attached Files\n" + "\n\n".join(attachment_info)
        expanded_message += "\n\nNote: For images, use the `analyze_image` tool with the provided path to examine the content."
        return expanded_message, temp_files
    
    return message, temp_files


# === Models ===

class Attachment(BaseModel):
    """添付ファイル"""
    type: str  # "image" or "file"
    name: str
    path: str
    mime_type: Optional[str] = None
    data: Optional[str] = None  # Base64 encoded data (optional, for web UI)


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    profile: str = "development"
    provider: str = "openrouter"
    model: Optional[str] = None  # OpenRouter用モデル名
    verbose: bool = False
    working_directory: Optional[str] = None  # 作業ディレクトリ（セッションごとに設定可能）
    attachments: Optional[list[Attachment]] = None  # 添付ファイル


class SessionCreate(BaseModel):
    title: str = "New Chat"
    profile: str = "development"
    working_directory: Optional[str] = None  # 作業ディレクトリ


class FileResponse(BaseModel):
    content: str
    line_count: int
    size: int
    path: str


class ApproveSessionRequest(BaseModel):
    tool: Optional[str] = None
    args: Dict[str, Any] = {}
    approved: Optional[bool] = None


class WorkdirRequest(BaseModel):
    working_directory: str


class ApprovalRespond(BaseModel):
    approved: bool


# === Routes ===

@app.get("/", response_class=HTMLResponse)
async def root():
    """メインページ"""
    with open(os.path.join(static_dir, "index.html"), "r") as f:
        return f.read()


@app.get("/api/browse-directories")
async def browse_directories(path: str = None):
    """
    ディレクトリ一覧を取得（フォルダ選択UI用）
    path が None の場合は作業ディレクトリを起点とする
    """
    # ベースディレクトリの決定
    # realpath を使用してシンボリックリンクを解決
    base_dir = os.path.realpath(os.getenv("MOCO_WORKING_DIRECTORY") or os.getcwd())

    if path is None:
        # デフォルト: 作業ディレクトリ
        base_paths = [
            {"path": base_dir, "name": "Workspace", "icon": "🏠"},
        ]
        # サブディレクトリがあれば追加
        for sub in ["src", "profiles", "workspace"]:
            full = os.path.join(base_dir, sub)
            if os.path.isdir(full):
                base_paths.append({"path": full, "name": sub, "icon": "📁"})
        
        return {"directories": base_paths, "current": base_dir}

    # パスの正規化と検証
    try:
        requested_path = os.path.normpath(path)
        if os.path.isabs(requested_path):
            target_path = os.path.realpath(requested_path)
        else:
            target_path = os.path.realpath(os.path.join(base_dir, requested_path))

        # ディレクトリトラバーサル対策: target_path が base_dir の配下にあるか確認
        if os.path.commonpath([base_dir, target_path]) != base_dir:
            return {
                "error": f"Access denied: {path} is outside the working directory",
                "directories": [],
                "current": path
            }
    except ValueError:
        # Windowsのドライブ跨ぎなどの場合に発生する可能性がある
        return {
            "error": f"Invalid path access: {path}",
            "directories": [],
            "current": path
        }

    if not os.path.exists(target_path):
        return {"error": f"Path not found: {path}", "directories": [], "current": path}

    if not os.path.isdir(target_path):
        return {"error": f"Not a directory: {path}", "directories": [], "current": path}

    try:
        items = os.listdir(target_path)
        directories = []
        for item in sorted(items):
            if item.startswith('.'):
                continue  # 隠しファイルをスキップ
            full_item_path = os.path.join(target_path, item)
            if os.path.isdir(full_item_path):
                # プロジェクトかどうかを判定
                is_project = any(
                    os.path.exists(os.path.join(full_item_path, marker))
                    for marker in [".git", "package.json", "pyproject.toml", "requirements.txt"]
                )
                directories.append({
                    "path": full_item_path,
                    "name": item,
                    "icon": "📦" if is_project else "📁",
                    "is_project": is_project
                })

        parent = os.path.dirname(target_path)
        # 親ディレクトリも制限内である場合のみ返す
        try:
            if os.path.commonpath([base_dir, parent]) != base_dir:
                parent = None
        except ValueError:
            parent = None

        return {
            "directories": directories,
            "current": target_path,
            "parent": parent if parent != target_path else None
        }
    except Exception as e:
        logger.exception(f"Error browsing directory: {target_path}")
        return {"error": str(e), "directories": [], "current": target_path}


@app.get("/api/profiles")
async def list_profiles():
    """利用可能なプロファイル一覧"""
    profiles_dir = _find_profiles_dir()
    if not os.path.exists(profiles_dir):
        return {"profiles": ["default"]}

    profiles = [
        d for d in os.listdir(profiles_dir)
        if os.path.isdir(os.path.join(profiles_dir, d)) and d != "__pycache__"
    ]
    return {"profiles": sorted(profiles)}


@app.get("/api/sessions")
async def list_sessions(limit: int = 20, profile: str = None):
    """セッション一覧（プロファイルでフィルタ可能）"""
    sessions = session_logger.list_sessions(limit=limit, profile=profile)
    return {"sessions": sessions}


@app.post("/api/sessions")
async def create_session(req: SessionCreate):
    """新規セッション作成"""
    orchestrator = get_orchestrator(req.profile)
    # Orchestrator.create_session は内部で profile=self.profile を渡すため、
    # metadata に profile を含めると二重指定になる
    metadata = {}
    if req.working_directory:
        metadata["working_directory"] = req.working_directory
        
    session_id = orchestrator.create_session(title=req.title, **metadata)
    return {"session_id": session_id, "title": req.title}


@app.post("/api/sessions/{session_id}/cancel")
async def cancel_task(session_id: str):
    """実行中のタスクをキャンセル"""
    success = request_cancel(session_id)
    return {"status": "success" if success else "not_found", "session_id": session_id}


@app.post("/api/sessions/{session_id}/approve")
async def create_approval(session_id: str, req: Dict[str, Any]):
    """
    承認要求の作成、またはセッション単位の承認応答（UI互換）。

    - tool/args がある場合: 承認要求を作成して approval_id を返す
    - approved のみある場合: そのセッションの最新 pending を承認/拒否する（UI向け）
    """
    session = session_logger.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    tool = req.get("tool")
    args = req.get("args") or {}
    approved = req.get("approved")

    if tool:
        approval_id = str(uuid.uuid4())
        await approval_manager.create_approval_request(approval_id, tool, args, session_id)
        return {"status": "ok", "approval_id": approval_id, "tool": tool, "args": args}

    if approved is not None:
        # respond to latest pending approval for the session
        with pending_approvals_lock:
            candidates = [
                (aid, item)
                for aid, item in pending_approvals.items()
                if item.get("session_id") == session_id
            ]
        if not candidates:
            return {"status": "not_found", "session_id": session_id}

        approval_id = candidates[-1][0]
        ok = await approval_manager.respond_to_approval(approval_id, bool(approved))
        return {"status": "ok" if ok else "not_found", "approval_id": approval_id, "decision": bool(approved)}

    # mimic FastAPI validation failure behavior for tests
    raise HTTPException(status_code=422, detail="tool field is required")


@app.post("/api/approvals/{approval_id}/respond")
async def respond_approval(approval_id: str, body: ApprovalRespond):
    ok = await approval_manager.respond_to_approval(approval_id, body.approved)
    if not ok:
        raise HTTPException(status_code=404, detail="Approval request not found or expired")
    return {"status": "ok", "approval_id": approval_id, "decision": bool(body.approved)}


@app.post("/api/sessions/{session_id}/workdir")
async def update_session_workdir(session_id: str, req: WorkdirRequest):
    """セッションの作業ディレクトリを更新"""
    try:
        # パスの存在確認と正規化
        path = os.path.abspath(req.working_directory)
        
        # セキュリティチェック: 許可されたルートディレクトリ内か確認
        # MOCO_ALLOWED_ROOTS が設定されていればそれを使用、なければ現在の作業ディレクトリ配下のみ許可
        allowed_roots_env = os.getenv("MOCO_ALLOWED_ROOTS", "")
        if allowed_roots_env:
            allowed_roots = [os.path.abspath(r.strip()) for r in allowed_roots_env.split(",") if r.strip()]
        else:
            # デフォルトは現在の OS 作業ディレクトリ（起動時）をルートとする
            allowed_roots = [os.path.abspath(os.getenv("MOCO_WORKING_DIRECTORY") or os.getcwd())]
            # /private/tmp なども開発用に許可リストに入れる必要がある場合があるが、
            # 明示的に指定されない限りは制限的に振る舞う
            if "/private/tmp" not in allowed_roots_env and req.working_directory.startswith("/private/tmp"):
                 allowed_roots.append("/private/tmp")

        is_allowed = False
        for root in allowed_roots:
            if os.path.commonpath([os.path.abspath(root), path]) == os.path.abspath(root):
                is_allowed = True
                break
        
        if not is_allowed:
            raise HTTPException(status_code=403, detail=f"Access denied: {req.working_directory} is outside allowed roots.")

        if not os.path.exists(path):
            raise HTTPException(status_code=400, detail=f"Directory not found: {req.working_directory}")
        if not os.path.isdir(path):
            raise HTTPException(status_code=400, detail=f"Path is not a directory: {req.working_directory}")

        # セッションメタデータの更新
        session = session_logger.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
            
        metadata = session.get("metadata") or {}
        metadata["working_directory"] = path
        session_logger.update_session(session_id, metadata=metadata)
        
        return {"status": "ok", "session_id": session_id, "working_directory": path}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """セッション詳細と履歴"""
    session = session_logger.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    history = session_logger._get_recent_messages(session_id, limit=100)
    return {
        "session": session,
        "messages": history
    }


@app.get("/api/file", response_model=FileResponse)
async def get_file_content(path: str):
    """
    指定されたファイルの情報を取得する。
    ディレクトリトラバーサル対策を施し、MOCO_WORKING_DIRECTORY配下のファイルのみアクセス可能。
    """
    # ベースディレクトリの決定
    base_dir = os.path.abspath(os.getenv("MOCO_WORKING_DIRECTORY") or os.getcwd())

    # パスの正規化と検証
    requested_path = os.path.normpath(path)
    if os.path.isabs(requested_path):
        # 絶対パスが指定された場合は、ベースディレクトリからの相対パスとして扱う
        requested_path = requested_path.lstrip(os.sep)

    target_path = os.path.abspath(os.path.join(base_dir, requested_path))

    # ディレクトリトラバーサル対策: target_path が base_dir の配下にあるか確認
    if os.path.commonpath([base_dir, target_path]) != base_dir:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: {path} is outside the working directory"
        )

    # ファイルの存在確認
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # ディレクトリでないことを確認
    if not os.path.isfile(target_path):
        raise HTTPException(status_code=400, detail=f"Path is not a file: {path}")

    try:
        # ファイル情報の取得
        size = os.path.getsize(target_path)

        # テキストファイルとして読み込み
        with open(target_path, 'r', encoding='utf-8') as f:
            content = f.read()

        line_count = len(content.splitlines())

        return FileResponse(
            content=content,
            line_count=line_count,
            size=size,
            path=path
        )
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="File is not a valid UTF-8 text file"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error reading file: {str(e)}"
        )


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """セッション削除"""
    try:
        session_logger.delete_session(session_id)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats")
async def get_stats(session_id: Optional[str] = None, scope: str = "all"):
    """統計データを取得"""
    try:
        from pathlib import Path
        db_path = Path(__file__).parent.parent.parent.parent / "data" / "optimizer" / "metrics.db"
        
        # デフォルトのレスポンス構造
        stats = {
            "today_avg_score": 0,
            "today_count": 0,
            "avg_score": 0,
            "count": 0,
            "success_rate": 0,
            "overall_metrics": {
                "avg_complexity": 0,
                "avg_delegation": 0,
                "todo_usage_rate": 0,
                "avg_history_turns": 0,
                "avg_summary_depth": 0,
                "avg_prompt_specificity": 0,
                "summaries": 0
            },
            "profile_stats": [],
            "recent_tasks": [],
            "score_trend": [],
            "agent_stats": {}
        }

        if scope == "session" and not session_id:
            return stats

        # ディレクトリ作成と初期化
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if not db_path.exists():
            # 新規作成時は空の統計を返す（テーブル作成後にデータがない状態と同じ）
            conn = sqlite3.connect(str(db_path))
            # ここでテーブル作成
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, ai_score REAL, task_summary TEXT, task_complexity REAL, delegation_count INTEGER, todo_used INTEGER, history_turns INTEGER, summary_depth INTEGER, prompt_specificity REAL, profile TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS agent_executions (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER, agent_name TEXT, inline_score REAL, tokens_input INTEGER, tokens_output INTEGER, execution_time_ms INTEGER, error_message TEXT, summary_depth INTEGER, history_turns INTEGER, FOREIGN KEY (request_id) REFERENCES metrics (id))")
            conn.commit()
            return stats
        
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # フィルタ条件の構築 (metricsテーブル)
        where_clause = "WHERE ai_score IS NOT NULL"
        params = []

        if scope == "session" and session_id:
            where_clause += " AND session_id = ?"
            params.append(session_id)
        elif scope == "today":
            where_clause += " AND timestamp >= date('now', 'localtime')"
        # "all" の場合は追加条件なし

        # フィルタ条件の構築 (agent_executionsテーブル)
        # agent_executionsにはsession_idやtimestampがないため、metricsとJOINが必要な場合がある
        ae_join = ""
        where_clause_ae = "WHERE 1=1"
        params_ae = []
        if scope == "session" and session_id:
            ae_join = "JOIN metrics m ON agent_executions.request_id = m.id"
            where_clause_ae = "WHERE m.session_id = ?"
            params_ae.append(session_id)
        elif scope == "today":
            ae_join = "JOIN metrics m ON agent_executions.request_id = m.id"
            where_clause_ae = "WHERE m.timestamp >= date('now', 'localtime')"

        # 今日の統計
        cursor.execute(f"""
            SELECT AVG(ai_score), COUNT(*),
                   SUM(CASE WHEN ai_score >= 0.7 THEN 1 ELSE 0 END) * 1.0 / COUNT(*)
            FROM metrics
            {where_clause}
        """, params)
        row = cursor.fetchone()
        today_avg = row[0] or 0
        today_count = row[1] or 0
        success_rate = row[2] or 0

        # 全体メトリクス
        cursor.execute(f"""
            SELECT
                AVG(task_complexity),
                AVG(delegation_count),
                AVG(CASE WHEN todo_used = 1 THEN 1.0 ELSE 0 END) * 100,
                AVG(history_turns),
                AVG(summary_depth),
                AVG(prompt_specificity),
                SUM(CASE WHEN summary_depth > 0 THEN 1 ELSE 0 END)
            FROM metrics
            {where_clause}
        """, params)
        metrics_row = cursor.fetchone()
        overall_metrics = {
            "avg_complexity": round(metrics_row[0] or 0, 1),
            "avg_delegation": round(metrics_row[1] or 0, 1),
            "todo_usage_rate": round(metrics_row[2] or 0, 1),
            "avg_history_turns": round(metrics_row[3] or 0, 1),
            "avg_summary_depth": round(metrics_row[4] or 0, 1),
            "avg_prompt_specificity": round(metrics_row[5] or 0, 1),
            "summaries": metrics_row[6] or 0
        }

        # プロファイル別統計
        cursor.execute(f"""
            SELECT profile, AVG(ai_score), COUNT(*)
            FROM metrics
            {where_clause}
            GROUP BY profile
            ORDER BY COUNT(*) DESC
            LIMIT 5
        """, params)
        profile_stats = [
            {"profile": r[0], "avg_score": r[1] or 0, "count": r[2]}
            for r in cursor.fetchall()
        ]

        # 最新タスク
        cursor.execute(f"""
            SELECT task_summary, ai_score, task_complexity, timestamp
            FROM metrics
            {where_clause}
            ORDER BY id DESC
            LIMIT 5
        """, params)
        recent_tasks = [
            {
                "task": r[0][:40] + "..." if r[0] and len(r[0]) > 40 else (r[0] or ""),
                "score": r[1] or 0,
                "complexity": r[2] or 0,
                "time": r[3].split("T")[1][:5] if r[3] and "T" in r[3] else ""
            }
            for r in cursor.fetchall()
        ]

        # スコア推移（直近10件）
        cursor.execute(f"""
            SELECT ai_score FROM metrics
            {where_clause}
            ORDER BY id DESC
            LIMIT 10
        """, params)
        score_trend = [r[0] for r in cursor.fetchall()][::-1]

        # エージェント別統計（新テーブル優先、フォールバックあり）
        agent_stats = {}

        # 新しい agent_executions テーブルからデータを取得
        try:
            cursor.execute(f"""
                SELECT
                    agent_name,
                    COUNT(*) as total,
                    AVG(inline_score) as avg_score,
                    AVG(tokens_input + tokens_output) as avg_tokens,
                    AVG(execution_time_ms) as avg_time_ms,
                    SUM(CASE WHEN inline_score >= 0.7 THEN 1 ELSE 0 END) as success_count,
                    SUM(CASE WHEN error_message IS NOT NULL THEN 1 ELSE 0 END) as error_count,
                    AVG(agent_executions.summary_depth),
                    AVG(agent_executions.history_turns),
                    SUM(CASE WHEN agent_executions.summary_depth > 0 THEN 1 ELSE 0 END) as summaries
                FROM agent_executions
                {ae_join}
                {where_clause_ae}
                GROUP BY agent_name
                ORDER BY total DESC
                LIMIT 10
            """, params_ae)
            rows = cursor.fetchall()

            if rows:
                for row in rows:
                    agent_name = row[0]
                    total = row[1]
                    agent_stats[agent_name] = {
                        "total": total,
                        "success": row[5] or 0,
                        "avg_score": round(row[2] or 0, 2),
                        "avg_tokens": round(row[3] or 0),
                        "avg_time_ms": round(row[4] or 0),
                        "success_rate": round((row[5] / total * 100) if total > 0 else 0, 1),
                        "error_rate": round((row[6] / total * 100) if total > 0 else 0, 1),
                        "avg_summary_depth": round(row[7] or 0, 1),
                        "avg_history_turns": round(row[8] or 0, 1),
                        "summaries": row[9] or 0
                    }
        except sqlite3.OperationalError:
            pass  # テーブルがまだ存在しない場合

        # 新テーブルにデータがない場合は旧方式でフォールバック
        if not agent_stats:
            cursor.execute(f"""
                SELECT agents_selected, ai_score, task_complexity, delegation_count, todo_used
                FROM metrics
                {where_clause} AND agents_selected IS NOT NULL
            """, params)
            rows = cursor.fetchall()

            from collections import defaultdict
            agent_counts = defaultdict(int)
            agent_scores = defaultdict(float)
            agent_success = defaultdict(int)
            agent_complexity = defaultdict(float)
            agent_delegation = defaultdict(float)
            agent_todo = defaultdict(int)

            for agents_json, score, complexity, delegation, todo in rows:
                try:
                    agents = json.loads(agents_json)
                    if isinstance(agents, list):
                        for agent in agents:
                            agent_counts[agent] += 1
                            agent_scores[agent] += score
                            if score >= 0.7:
                                agent_success[agent] += 1
                            agent_complexity[agent] += (complexity or 0)
                            agent_delegation[agent] += (delegation or 0)
                            if todo:
                                agent_todo[agent] += 1
                except Exception:
                    continue

            for agent, count in agent_counts.items():
                avg_score = agent_scores[agent] / count
                agent_stats[agent] = {
                    "total": count,
                    "success": agent_success[agent],
                    "avg_score": round(avg_score, 2),
                    "avg_complexity": round(agent_complexity[agent] / count, 1),
                    "avg_delegation": round(agent_delegation[agent] / count, 1),
                    "todo_usage": round(agent_todo[agent] / count * 100, 1),
                    "summaries": 0,
                    "avg_history_turns": 0
                }

            # 上位10件に絞る（countの多い順）
            sorted_agents = sorted(agent_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:10]
            agent_stats = dict(sorted_agents)

        conn.close()
        
        return {
            "today_avg_score": round(today_avg, 2),
            "today_count": today_count,
            "avg_score": round(today_avg, 2),
            "count": today_count,
            "success_rate": round(success_rate * 100, 1),
            "overall_metrics": overall_metrics,
            "profile_stats": profile_stats,
            "recent_tasks": recent_tasks,
            "score_trend": score_trend,
            "agent_stats": agent_stats
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """チャット（非ストリーミング）- WhatsApp/モバイル連携用"""
    
    orchestrator = get_orchestrator(
        req.profile,
        req.provider,
        req.verbose,
        req.working_directory
    )

    # セッションIDがない場合は新規作成
    session_id = req.session_id
    if not session_id:
        session_id = orchestrator.create_session(title=req.message[:50])

    # 添付ファイルを処理
    message = req.message
    if req.attachments:
        expanded_message, _ = process_attachments(req.attachments, req.message)
        message = expanded_message

    # セッション準備
    session_id, history = orchestrator._prepare_session(message, session_id)

    # アーティファクトをクリア（リクエスト開始時）
    clear_artifacts()
    
    # 非同期で実行（イベントループの競合を回避）
    response = await orchestrator.process_message(message, session_id, history)
    
    # 送信待ちのアーティファクトを取得
    artifacts = get_pending_artifacts()
    print(f"🔧 [api.py] artifacts取得: {len(artifacts)}件 - {artifacts}")

    return {
        "response": response,
        "session_id": session_id,
        "artifacts": artifacts
    }


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    """チャット（ストリーミング）- Server-Sent Events with real-time tool updates"""

    # イベントキュー（スレッド間通信用）
    event_queue = queue.Queue()

    # thinking イベントのバッチ化用
    thinking_buffer = ""
    last_thinking_time = 0
    last_agent_name = "orchestrator"

    # 進捗コールバック
    def progress_callback(event_type: str, name: str = None, detail: str = "", agent_name: str = None, parent_agent: str = None, status: str = "running", tool_name: str = None, content: str = None, result: str = None, **kwargs):
        nonlocal thinking_buffer, last_thinking_time, last_agent_name
        
        current_agent = agent_name or last_agent_name or "orchestrator"
        last_agent_name = current_agent

        if event_type == "thinking":
            thinking_buffer += (content or "")
            current_time = time.time()
            # 100文字以上、または0.2秒経過したら送信
            if len(thinking_buffer) >= 100 or (current_time - last_thinking_time) >= 0.2:
                event_queue.put({
                    "type": "thinking",
                    "content": thinking_buffer,
                    "agent": current_agent
                })
                thinking_buffer = ""
                last_thinking_time = current_time
            return

        # 思考以外のイベントが発生した場合はバッファをフラッシュ（順序維持）
        if thinking_buffer:
            event_queue.put({
                "type": "thinking",
                "content": thinking_buffer,
                "agent": current_agent
            })
            thinking_buffer = ""
            
        if event_type == "flush":
            return

        if event_type == "chunk":
            event_queue.put({
                "type": "chunk",
                "content": content,
                "agent": current_agent
            })
            return

        # agent_name が明示的に None の場合をガード
        agent_name = agent_name or "orchestrator"
        
        # 名前が提供されていない場合はエージェント名を使用（recallなどの場合）
        display_name = name or agent_name or ""
        # アイコンを削除してツール名/エージェント名のみにする
        clean_name = display_name
        if display_name and " " in display_name:
            parts = display_name.split()
            if parts:
                clean_name = parts[-1]

        # インサイトパネル用のイベント送信
        if event_type == "recall":
            results = kwargs.get("results", [])
            for res in results:
                event_queue.put({
                    "type": "recall",
                    "recall_type": "Memory",
                    "query": detail or "Semantic Recall",
                    "details": res.get("content", "") if isinstance(res, dict) else str(res)
                })
        elif event_type == "delegate" and status == "running":
            event_queue.put({
                "type": "recall",
                "recall_type": "Delegation",
                "query": f"→ @{clean_name}",
                "details": detail
            })
        elif event_type == "tool" and status == "completed":
            # ツール実行結果もインサイトに表示
            event_queue.put({
                "type": "recall",
                "recall_type": "Tool",
                "query": f"🛠️ {tool_name or clean_name}",
                "details": result
            })

        # app.js が期待する形式（agent, parent, tool, event, status）に統一
        data = {
            "type": "progress",
            "agent": agent_name,
            "parent": parent_agent,
            "tool": tool_name or (clean_name if event_type == "tool" else None),
            "event": event_type,
            "status": status,
            "name": clean_name,
            "detail": detail
        }
        event_queue.put(data)

    # Orchestrator をコールバック付きで作成
    # 作業ディレクトリ: リクエスト > 環境変数 > カレントディレクトリ
    work_dir = req.working_directory or os.getenv("MOCO_WORKING_DIRECTORY") or os.getcwd()
    orchestrator = Orchestrator(
        profile=req.profile,
        provider=req.provider,
        model=req.model,  # OpenRouter用モデル名
        session_logger=session_logger,
        verbose=req.verbose,
        progress_callback=progress_callback,
        working_directory=work_dir
    )

    session_id = req.session_id
    if not session_id:
        session_id = orchestrator.create_session(title=req.message[:50])

    # 添付ファイルを処理してメッセージを拡張
    expanded_message, temp_files = process_attachments(req.attachments, req.message)

    # キャンセルイベントを確実にクリアしてから新規登録
    # (過去のリクエストでキャンセル状態が残っているのを防ぐ)
    clear_cancel_event(session_id)
    create_cancel_event(session_id)

    # 結果を格納する変数
    result_holder = {"response": None, "error": None, "cancelled": False, "temp_files": temp_files}
    stop_event = threading.Event()

    # プロンプトの拡張
    expanded_message = req.message
    if req.attachments:
        expanded_message += "\n\nFiles attached to this message:"
        for att in req.attachments:
            att_name = att.get("name", "unknown")
            att_path = att.get("path") or (att.get("data")[:50] + "..." if att.get("data") else "unknown")
            att_type = att.get("type", "file")
            if att_type == "image":
                expanded_message += f"\n- [Image: {att_name}] Path: {att_path}"
                expanded_message += "\n  Note: Use the `analyze_image` tool to see this image if needed."
            else:
                expanded_message += f"\n- [File: {att_name}] Path: {att_path}"
                expanded_message += f"\n  Note: Use the `file_upload` tool to read this file."

    def run_orchestrator():
        try:
            result_holder["response"] = orchestrator.run_sync(expanded_message, session_id=session_id)
            # 完了直前に強制フラッシュ
            progress_callback(event_type="flush")
        except OperationCancelled:
            result_holder["cancelled"] = True
            # キャンセル時は特別なイベントを投げる
            event_queue.put({"type": "cancelled", "message": "Task was cancelled by user."})
        except Exception as e:
            result_holder["error"] = str(e)
        finally:
            # clear_cancel_event はバックエンド側の check_cancelled 内でも呼ばれる可能性があるが、
            # 万が一の漏れを防ぐためここでも呼ぶ。ただし二重呼び出しは問題ない設計。
            clear_cancel_event(session_id)
            if not stop_event.is_set():
                event_queue.put({"type": "done"})

    # バックグラウンドで実行
    thread = threading.Thread(target=run_orchestrator, daemon=True)
    thread.start()

    async def generate():
        # 開始イベント
        yield f"data: {json.dumps({'type': 'start', 'session_id': session_id})}\n\n"
        await asyncio.sleep(0.01)

        has_sent_chunks = False

        try:
            while True:
                try:
                    # ノンブロッキングでキューをチェック
                    event = event_queue.get(timeout=0.1)

                    if event["type"] == "done":
                        # 完了 - チャンクが一度も送られていない場合のみ、最終結果を送信
                        if result_holder["cancelled"]:
                            # キャンセルメッセージは別途送信済み（type: cancelled）だが、
                            # クライアント側の処理確実化のために status: cancelled も送る
                            yield f"data: {json.dumps({'type': 'status', 'status': 'cancelled', 'content': 'Operation cancelled.'})}\n\n"
                        elif result_holder["error"]:
                            yield f"data: {json.dumps({'type': 'error', 'message': result_holder['error']})}\n\n"
                        elif not has_sent_chunks:
                            response = result_holder["response"] or ""
                            # verbose でない場合はフィルタリング
                            response = filter_response_for_display(response, req.verbose)
                            # 結果をチャンクで送信
                            chunk_size = 100
                            for i in range(0, len(response), chunk_size):
                                chunk = response[i:i+chunk_size]
                                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
                                await asyncio.sleep(0.01)

                        yield f"data: {json.dumps({'type': 'done'})}\n\n"
                        break
                    elif event["type"] == "chunk":
                        has_sent_chunks = True
                        yield f"data: {json.dumps(event)}\n\n"
                        await asyncio.sleep(0.01)
                    else:
                        # 進捗イベント
                        yield f"data: {json.dumps(event)}\n\n"
                        await asyncio.sleep(0.01)

                except queue.Empty:
                    # キューが空 - 少し待つ
                    await asyncio.sleep(0.05)
        finally:
            stop_event.set()
            # 一時ファイルのクリーンアップ
            for temp_file in result_holder.get("temp_files", []):
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                except Exception as e:
                    logger.warning(f"Failed to remove temp file {temp_file}: {e}")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Nginxなどのバッファリングを無効化
        }
    )


@app.websocket("/ws/gateway")
async def websocket_gateway(websocket: WebSocket, token: Optional[str] = Query(None)):
    """モバイル接続用ゲートウェイ WebSocket"""
    from moco.gateway.server import handle_gateway_connection
    await handle_gateway_connection(websocket, token)


@app.post("/webhook/{channel}")
async def webhook_handler(channel: str, request: Request):
    """
    外部サービス（LINE, Telegram等）からのWebhookを受信するエンドポイント。
    """
    from moco.adapters.line_adapter import LINEAdapter
    from moco.adapters.telegram_adapter import TelegramAdapter
    
    # 署名検証のために生のボディを取得
    body = await request.body()
    body_str = body.decode("utf-8")
    try:
        payload = json.loads(body_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # チャネルに応じたアダプターの初期化
    adapter = None
    if channel == "line":
        token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
        secret = os.getenv("LINE_CHANNEL_SECRET")
        if token:
            adapter = LINEAdapter(token, secret)
            # 署名検証
            signature = request.headers.get("X-Line-Signature")
            if not signature or not adapter.verify_signature(body_str, signature):
                logger.warning("Invalid LINE signature")
                raise HTTPException(status_code=403, detail="Invalid signature")
    elif channel == "telegram":
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token:
            adapter = TelegramAdapter(token)
            # Telegramシークレットトークン検証
            tg_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
            if tg_secret:
                header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
                if not header_secret or not secrets.compare_digest(header_secret, tg_secret):
                    logger.warning("Invalid Telegram secret token")
                    raise HTTPException(status_code=403, detail="Invalid secret token")
    
    if not adapter:
        # フォールバック: パラメータをそのまま使う簡易モード
        return await _simple_webhook_handler(channel, payload)

    # 1. Webhookペイロードの正規化
    normalized_msgs = await adapter.handle_webhook(payload)
    if not normalized_msgs:
        return {"status": "ignored"}

    processor = MediaProcessor()
    
    for msg in normalized_msgs:
        processed_text = msg.text or ""
        attachments_info = []

        # 2. メディア処理 (画像リサイズ、音声文字起こし等)
        if msg.media:
            for m in msg.media:
                data = await adapter.download_media(m)
                if not data:
                    continue
                
                if m.type == "image":
                    processed = await processor.process_image(data)
                    path = processor.save_temp(processed.data, f"line_{msg.message_id}.jpg")
                    attachments_info.append(f"[Image processed] Path: {path}")
                elif m.type == "audio":
                    processed = await processor.process_audio(data, m.mime_type)
                    transcript = processed.metadata.get("transcript")
                    if transcript:
                        attachments_info.append(f"[Audio transcript] {transcript}")
                else:
                    path = processor.save_temp(data, m.filename or "file")
                    attachments_info.append(f"[File] Path: {path}")

        combined_text = processed_text
        if attachments_info:
            combined_text += "\n\n" + "\n".join(attachments_info)

        if not combined_text.strip():
            continue

        # 3. セッション管理とOrchestrator実行
        session_id = f"{channel}_{msg.conversation_id}"
        orchestrator = get_orchestrator(profile="development")
        
        # 履歴が存在するか確認（存在しなければ作成）
        if not session_logger.get_session(session_id):
            session_logger.create_session(session_id, title=f"{channel.capitalize()} Chat")

        # 承認リクエスト用の情報を OutgoingMessage に含めるための準備 (LINE等)
        async def mock_approval_notification(approval_id, tool, args, sid):
            if sid == session_id:
                from moco.adapters.base import OutgoingMessage
                await adapter.send_message(
                    msg.conversation_id, 
                    OutgoingMessage(
                        text=f"Approval required for {tool}",
                        raw_payload={"approval_id": approval_id, "tool": tool, "args": args}
                    )
                )

        # 承認イベントをフックするために一時的に登録（将来的には ApprovalManager 側でフックするのが望ましい）
        # ここでは簡易的にバックグラウンド処理内で完結させる

        # バックグラウンド処理
        async def process_task(sid, text, msg_obj):
            response = await asyncio.to_thread(orchestrator.run_sync, text, session_id=sid)
            # 送信
            from moco.adapters.base import OutgoingMessage
            await adapter.send_message(msg_obj.conversation_id, OutgoingMessage(text=response))

        asyncio.create_task(process_task(session_id, combined_text, msg))

    return {"status": "accepted"}


async def _simple_webhook_handler(channel: str, request: Dict[str, Any]):
    """互換性のための簡易Webhookハンドラー"""
    # sender_name = request.get("sender_name", "Mobile User")
    text = request.get("text", "")
    session_id = request.get("session_id")

    if not text:
        return {"status": "ignored", "reason": "empty text"}

    orchestrator = get_orchestrator(profile="development")
    if not session_id:
        session_id = orchestrator.create_session(title=f"Chat from {channel}")

    async def process_task():
        response = await asyncio.to_thread(orchestrator.run_sync, text, session_id=session_id)
        payload = {
            "type": "chat_response",
            "session_id": session_id,
            "response": response
        }
        for ws in list(approval_manager.gateway_clients.values()):
            try:
                await ws.send_json(payload)
            except Exception:
                continue

    asyncio.create_task(process_task())
    return {"status": "accepted", "session_id": session_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
