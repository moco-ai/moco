#!/usr/bin/env python3
"""
Slack ↔ moco 連携 (Socket Mode) with Streaming Support

使い方:
1. moco ui を起動: moco ui
2. Slack Appを作成し、以下のトークンを取得して環境変数に設定:
   - SLACK_BOT_TOKEN: xoxb-...
   - SLACK_APP_TOKEN: xapp-...
3. このスクリプトを実行: python slack_moco.py
"""

import os
import re
import json
import httpx
import base64
import logging
import threading
import time
from typing import Dict, Any, List, Optional
from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("moco-slack")

# 設定
# MOCO_API_URL は従来 http://localhost:8000/api/chat だったので、ベースURLを抽出
_moco_url = os.getenv("MOCO_API_URL", "http://localhost:8000")
# /api/chat が含まれていれば除去してベースURLを取得
if _moco_url.endswith("/api/chat"):
    MOCO_API_BASE = _moco_url[:-9]  # len("/api/chat") = 9
elif _moco_url.endswith("/api/chat/"):
    MOCO_API_BASE = _moco_url[:-10]
else:
    MOCO_API_BASE = _moco_url.rstrip("/")
MOCO_STREAM_URL = f"{MOCO_API_BASE}/api/chat/stream"
DEFAULT_PROFILE = "cursor"
DEFAULT_PROVIDER = "openrouter"

# Slackトークン
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")

# グローバル変数（main()内で初期化）
web_client: Optional[WebClient] = None
socket_client: Optional[SocketModeClient] = None


class SlackStreamManager:
    """Slackメッセージのリアルタイム更新を管理"""
    
    SLACK_MAX_MESSAGE_SIZE = 3500  # Slack制限は4000だが余裕を持たせる
    UPDATE_INTERVAL = 2.0  # 秒 (レート制限対策)
    
    def __init__(self, channel: str, thread_ts: str):
        self.channel = channel
        self.thread_ts = thread_ts
        self.full_content = ""
        self.status_line = ""
        self.message_ts: Optional[str] = None  # 投稿済みメッセージのts
        self.chunks: List[Dict[str, Any]] = []  # 複数メッセージ用
        self.last_update_time = 0.0
        self._lock = threading.Lock()
        self.is_final = False
    
    def _split_text(self, text: str) -> List[str]:
        """長いテキストを複数のチャンクに分割"""
        if len(text) <= self.SLACK_MAX_MESSAGE_SIZE:
            return [text] if text else []
        
        chunks = []
        while text:
            if len(text) <= self.SLACK_MAX_MESSAGE_SIZE:
                chunks.append(text)
                break
            # 改行で分割を試みる
            split_point = text.rfind('\n', 0, self.SLACK_MAX_MESSAGE_SIZE)
            if split_point < self.SLACK_MAX_MESSAGE_SIZE // 2:
                split_point = self.SLACK_MAX_MESSAGE_SIZE
            chunks.append(text[:split_point])
            text = text[split_point:].lstrip()
        return chunks
    
    def update_content(self, chunk: str):
        """コンテンツを追加してSlackを更新"""
        with self._lock:
            self.full_content += chunk
            self._maybe_update_slack()
    
    def set_status(self, status: str):
        """ステータス行を設定"""
        with self._lock:
            if status:
                self.status_line = f"\n\n---\n⏳ {status}"
            else:
                self.status_line = ""
            self._maybe_update_slack()
    
    def _maybe_update_slack(self):
        """スロットリング付きでSlackを更新"""
        now = time.time()
        
        # 最終更新でなければインターバルをチェック
        if not self.is_final and (now - self.last_update_time) < self.UPDATE_INTERVAL:
            return
        
        text_to_display = self.full_content
        if not self.is_final:
            text_to_display += "..."
            if self.status_line:
                text_to_display += self.status_line
        
        if not text_to_display.strip():
            return
        
        # テキストを分割
        new_chunks = self._split_text(text_to_display)
        if not new_chunks:
            return
        
        try:
            # 既存のチャンクを更新、必要なら新しいチャンクを追加
            for i, chunk_text in enumerate(new_chunks):
                if i < len(self.chunks):
                    # 既存メッセージを更新
                    if self.chunks[i]["content"] != chunk_text:
                        web_client.chat_update(
                            channel=self.channel,
                            ts=self.chunks[i]["ts"],
                            text=chunk_text
                        )
                        self.chunks[i]["content"] = chunk_text
                else:
                    # 新しいメッセージを投稿
                    result = web_client.chat_postMessage(
                        channel=self.channel,
                        text=chunk_text,
                        thread_ts=self.thread_ts
                    )
                    self.chunks.append({
                        "ts": result["ts"],
                        "content": chunk_text
                    })
                    if i == 0:
                        self.message_ts = result["ts"]
            
            self.last_update_time = now
            
        except Exception as e:
            # レート制限などのエラーは無視（次の更新で再試行）
            if "ratelimited" in str(e).lower():
                logger.warning(f"⚠️ Rate limited, will retry")
            else:
                logger.error(f"⚠️ Slack更新エラー: {e}")
    
    def finalize(self, final_content: Optional[str] = None):
        """最終更新（ステータス行を削除）"""
        with self._lock:
            self.is_final = True
            if final_content is not None:
                self.full_content = final_content
            self.status_line = ""
            self._maybe_update_slack()
            
            # メッセージが一つも投稿されていない場合
            if not self.chunks and self.full_content:
                try:
                    result = web_client.chat_postMessage(
                        channel=self.channel,
                        text=self.full_content,
                        thread_ts=self.thread_ts
                    )
                    self.message_ts = result["ts"]
                except Exception as e:
                    logger.error(f"⚠️ 最終投稿エラー: {e}")

# ユーザーごとの設定 (メモリ保持)
# { "channel_id:user_id": { ... } }
user_settings: Dict[str, Dict[str, Any]] = {}


def get_settings_key(event: Dict[str, Any]) -> str:
    channel = event.get("channel")
    user = event.get("user")
    return f"{channel}:{user}"


def get_user_settings(key: str) -> dict:
    if key not in user_settings:
        user_settings[key] = {
            "session_id": None,
            "profile": DEFAULT_PROFILE,
            "provider": DEFAULT_PROVIDER
        }
    return user_settings[key]


def process_slack_files(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Slackの添付ファイルをmoco形式に変換"""
    attachments = []
    for f in files:
        mimetype = f.get("mimetype", "")
        if mimetype.startswith("image/"):
            try:
                url = f.get("url_private")
                headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
                response = httpx.get(url, headers=headers)
                if response.status_code == 200:
                    b64_data = base64.b64encode(response.content).decode("utf-8")
                    attachments.append({
                        "type": "image",
                        "name": f.get("name", "slack_image.jpg"),
                        "mime_type": mimetype,
                        "data": b64_data
                    })
                    logger.info(f"✅ 画像取得完了: {f.get('name')}")
            except Exception as e:
                logger.error(f"⚠️ 画像取得エラー: {e}")
    return attachments


def stream_moco_response(payload: Dict[str, Any], stream_manager: SlackStreamManager, settings: dict):
    """moco APIからストリーミングでレスポンスを取得してSlackを更新"""
    try:
        with httpx.Client(timeout=300.0) as http:
            with http.stream("POST", MOCO_STREAM_URL, json=payload) as response:
                if response.status_code != 200:
                    stream_manager.finalize(f"❌ moco エラー: {response.status_code}")
                    return
                
                buffer = ""
                current_tool = None  # 現在実行中のツール名
                
                for chunk in response.iter_text():
                    buffer += chunk
                    
                    # SSEイベントを処理
                    while "\n\n" in buffer:
                        event_str, buffer = buffer.split("\n\n", 1)
                        
                        for line in event_str.split("\n"):
                            if line.startswith("data: "):
                                try:
                                    data = json.loads(line[6:])
                                    event_type = data.get("type")
                                    
                                    if event_type == "start":
                                        # 開始イベント - session_id を取得
                                        new_session_id = data.get("session_id")
                                        if new_session_id:
                                            settings["session_id"] = new_session_id
                                    
                                    elif event_type == "chunk":
                                        # コンテンツチャンク
                                        content = data.get("content", "")
                                        stream_manager.update_content(content)
                                    
                                    elif event_type == "thinking":
                                        # 思考中（ステータス表示）- verbose相当
                                        agent = data.get("agent", "moco")
                                        stream_manager.set_status(f"💭 {agent} が考え中...")
                                    
                                    elif event_type == "progress":
                                        # 進捗イベント（ツール実行、デリゲーションなど）
                                        event_name = data.get("event", "")
                                        status = data.get("status", "")
                                        tool_name = data.get("tool") or data.get("name", "")
                                        agent = data.get("agent", "")
                                        
                                        if event_name == "tool":
                                            if status == "running":
                                                current_tool = tool_name
                                                stream_manager.set_status(f"🔧 `{tool_name}` を実行中...")
                                            elif status == "completed":
                                                stream_manager.set_status("")
                                                current_tool = None
                                        elif event_name == "delegate":
                                            if status == "running":
                                                stream_manager.set_status(f"🤖 @{tool_name} に委任中...")
                                            elif status == "completed":
                                                stream_manager.set_status("")
                                    
                                    elif event_type == "recall":
                                        # メモリ/インサイト呼び出し
                                        recall_type = data.get("recall_type", "")
                                        query = data.get("query", "")
                                        if recall_type and query:
                                            stream_manager.set_status(f"📚 {recall_type}: {query[:30]}...")
                                    
                                    elif event_type == "done":
                                        # 完了
                                        stream_manager.finalize()
                                        return
                                    
                                    elif event_type == "cancelled":
                                        # キャンセル
                                        stream_manager.finalize("⚠️ キャンセルされました")
                                        return
                                    
                                    elif event_type == "error":
                                        # エラー
                                        error_msg = data.get("message", "不明なエラー")
                                        stream_manager.finalize(f"❌ エラー: {error_msg}")
                                        return
                                
                                except json.JSONDecodeError:
                                    pass
                
                # 残りのバッファを処理
                stream_manager.finalize()
                
    except httpx.TimeoutException:
        stream_manager.finalize("❌ タイムアウト: moco APIからの応答がありません")
    except httpx.ConnectError:
        stream_manager.finalize("❌ 接続エラー: moco APIに接続できません")
    except Exception as e:
        logger.error(f"❌ ストリーミングエラー: {e}")
        stream_manager.finalize(f"❌ エラー: {e}")


def handle_message(client: SocketModeClient, req: SocketModeRequest):
    if req.type != "events_api":
        return

    # Acknowledge the request
    response = SocketModeResponse(envelope_id=req.envelope_id)
    client.send_socket_mode_response(response)

    event = req.payload.get("event", {})
    event_type = event.get("type")
    
    # ボット自身のメッセージ、あるいはsubtypeがある場合（メッセージ削除など）は無視
    if event.get("bot_id") or event.get("subtype"):
        return

    # app_mention または message(DM) の場合のみ処理
    if event_type not in ["app_mention", "message"]:
        return

    text = event.get("text", "")
    channel = event.get("channel")
    user = event.get("user")
    ts = event.get("ts")
    thread_ts = event.get("thread_ts") or ts

    if not text:
        return

    key = get_settings_key(event)
    settings = get_user_settings(key)

    # コマンド処理
    text_strip = text.strip()
    # メンション部分を削除 (例: <@U12345> /status -> /status)
    cmd_text = re.sub(r'<@U[A-Z0-9]+>\s*', '', text_strip).strip()

    if cmd_text.startswith("/"):
        handle_command(cmd_text, channel, thread_ts, settings)
        return

    # ファイル処理
    files = event.get("files", [])
    attachments = process_slack_files(files)

    # moco API呼び出し（ストリーミング）
    logger.info(f"🚀 moco に送信中... User:{user} [{settings['profile']}/{settings['provider']}]")
    
    payload = {
        "message": cmd_text,
        "session_id": settings["session_id"],
        "profile": settings["profile"],
        "provider": settings["provider"]
    }
    # モデルが設定されている場合は追加
    if settings.get("model"):
        payload["model"] = settings["model"]
    
    if attachments:
        payload["attachments"] = attachments
        if not cmd_text:
            payload["message"] = "この画像について教えてください。"

    # ストリーミングマネージャーを作成
    stream_manager = SlackStreamManager(channel, thread_ts)
    
    # バックグラウンドでストリーミング処理
    def run_stream():
        try:
            stream_moco_response(payload, stream_manager, settings)
            logger.info("📤 ストリーミング完了")
        except Exception as e:
            logger.error(f"❌ ストリーミングエラー: {e}")
            stream_manager.finalize(f"❌ エラー: {e}")
    
    thread = threading.Thread(target=run_stream)
    thread.start()

def handle_command(text: str, channel: str, thread_ts: str, settings: dict):
    parts = text.split()
    cmd = parts[0].lower().lstrip("/")
    args = parts[1:] if len(parts) > 1 else []
    
    reply = ""
    
    # セッション管理
    if cmd in ["clear", "new"]:
        settings["session_id"] = None
        reply = "🗑️ セッションをクリアしました（新しい会話を開始）"
    
    # プロファイル変更
    elif cmd == "profile":
        if args:
            settings["profile"] = args[0]
            reply = f"✅ プロファイルを変更: `{args[0]}`"
        else:
            reply = f"📋 現在のプロファイル: `{settings['profile']}`\n使用例: `/profile cursor`"
    
    # プロバイダ変更
    elif cmd == "provider":
        if args:
            settings["provider"] = args[0]
            reply = f"✅ プロバイダを変更: `{args[0]}`"
        else:
            providers = ["openrouter", "gemini", "openai", "anthropic"]
            reply = f"📋 現在のプロバイダ: `{settings['provider']}`\n利用可能: {', '.join(providers)}\n使用例: `/provider openrouter`"
    
    # モデル変更
    elif cmd == "model":
        if args:
            settings["model"] = args[0]
            reply = f"✅ モデルを変更: `{args[0]}`"
        else:
            current_model = settings.get("model", "(デフォルト)")
            reply = f"📋 現在のモデル: `{current_model}`\n使用例: `/model google/gemini-2.0-flash`"
    
    # ステータス表示
    elif cmd == "status":
        session_display = settings['session_id'][:8] + "..." if settings['session_id'] else "(新規)"
        model_display = settings.get("model", "(デフォルト)")
        reply = (
            f"📊 *moco 設定*\n"
            f"• プロファイル: `{settings['profile']}`\n"
            f"• プロバイダ: `{settings['provider']}`\n"
            f"• モデル: `{model_display}`\n"
            f"• セッション: `{session_display}`"
        )
    
    # セッション情報
    elif cmd == "session":
        if settings['session_id']:
            reply = f"📝 セッションID: `{settings['session_id']}`"
        else:
            reply = "📝 セッション: (未開始 - 次のメッセージで自動作成されます)"
    
    # ツール一覧（API経由で取得）
    elif cmd == "tools":
        try:
            with httpx.Client(timeout=10.0) as http:
                resp = http.get(f"{MOCO_API_BASE}/api/tools", params={"profile": settings["profile"]})
                if resp.status_code == 200:
                    data = resp.json()
                    tools = data.get("tools", [])
                    if tools:
                        tool_list = "\n".join([f"• `{t}`" for t in sorted(tools)[:20]])
                        reply = f"🔧 *利用可能なツール* ({len(tools)}個)\n{tool_list}"
                        if len(tools) > 20:
                            reply += f"\n... 他 {len(tools) - 20} 個"
                    else:
                        reply = "🔧 ツールが見つかりません"
                else:
                    reply = "⚠️ ツール一覧の取得に失敗しました"
        except Exception as e:
            reply = f"⚠️ ツール一覧の取得に失敗: {e}"
    
    # エージェント一覧（API経由で取得）
    elif cmd == "agents":
        try:
            with httpx.Client(timeout=10.0) as http:
                resp = http.get(f"{MOCO_API_BASE}/api/agents", params={"profile": settings["profile"]})
                if resp.status_code == 200:
                    data = resp.json()
                    agents = data.get("agents", [])
                    if agents:
                        agent_list = "\n".join([f"• `{a['name']}`: {a.get('description', '')[:50]}" for a in agents[:10]])
                        reply = f"🤖 *利用可能なエージェント* ({len(agents)}個)\n{agent_list}"
                    else:
                        reply = "🤖 エージェントが見つかりません"
                else:
                    reply = "⚠️ エージェント一覧の取得に失敗しました"
        except Exception as e:
            reply = f"⚠️ エージェント一覧の取得に失敗: {e}"
    
    # プロファイル一覧
    elif cmd == "profiles":
        try:
            with httpx.Client(timeout=10.0) as http:
                resp = http.get(f"{MOCO_API_BASE}/api/profiles")
                if resp.status_code == 200:
                    data = resp.json()
                    profiles = data.get("profiles", [])
                    if profiles:
                        current = settings["profile"]
                        profile_list = "\n".join([f"{'→' if p == current else '•'} `{p}`" for p in sorted(profiles)])
                        reply = f"📂 *利用可能なプロファイル*\n{profile_list}"
                    else:
                        reply = "📂 プロファイルが見つかりません"
                else:
                    reply = "⚠️ プロファイル一覧の取得に失敗しました"
        except Exception as e:
            reply = f"⚠️ プロファイル一覧の取得に失敗: {e}"
    
    # ヘルプ
    elif cmd == "help":
        reply = (
            "📚 *moco Slack コマンド*\n\n"
            "*セッション管理*\n"
            "• `/new` `/clear` - 新しいセッションを開始\n"
            "• `/session` - セッション情報を表示\n"
            "• `/status` - 現在の設定を表示\n\n"
            "*設定変更*\n"
            "• `/profile [name]` - プロファイル表示/変更\n"
            "• `/profiles` - プロファイル一覧\n"
            "• `/provider [name]` - プロバイダ表示/変更\n"
            "• `/model [name]` - モデル表示/変更\n\n"
            "*情報*\n"
            "• `/tools` - 利用可能なツール一覧\n"
            "• `/agents` - 利用可能なエージェント一覧\n"
            "• `/help` - このヘルプを表示"
        )
    
    else:
        reply = f"❓ 不明なコマンド: `/{cmd}`\n`/help` でコマンド一覧を表示"

    web_client.chat_postMessage(channel=channel, text=reply, thread_ts=thread_ts)


def main():
    """Start the Slack gateway"""
    global web_client, socket_client
    
    # トークンチェック
    if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
        print("❌ エラー: SLACK_BOT_TOKEN と SLACK_APP_TOKEN を環境変数に設定してください。")
        exit(1)
    
    # クライアント初期化
    web_client = WebClient(token=SLACK_BOT_TOKEN)
    socket_client = SocketModeClient(
        app_token=SLACK_APP_TOKEN,
        web_client=web_client
    )
    
    # ボットのユーザーIDを取得（ループ防止用）
    auth_test = web_client.auth_test()
    bot_user_id = auth_test["user_id"]
    logger.info(f"🤖 Bot User ID: {bot_user_id}")
    logger.info(f"📡 moco API: {MOCO_STREAM_URL}")

    socket_client.socket_mode_request_listeners.append(handle_message)
    
    logger.info("⚡ Socket Mode Client 接続中...")
    socket_client.connect()
    
    from threading import Event
    Event().wait()


if __name__ == "__main__":
    main()
