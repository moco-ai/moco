#!/usr/bin/env python3
"""
Slack ↔ moco 連携 (Socket Mode)

使い方:
1. moco ui を起動: moco ui
2. Slack Appを作成し、以下のトークンを取得して環境変数に設定:
   - SLACK_BOT_TOKEN: xoxb-...
   - SLACK_APP_TOKEN: xapp-...
3. このスクリプトを実行: python slack_moco.py
"""

import os
import httpx
import base64
import logging
from typing import Dict, Any, List
from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("moco-slack")

# 設定
MOCO_API_URL = os.getenv("MOCO_API_URL", "http://localhost:8000/api/chat")
DEFAULT_PROFILE = "cursor"
DEFAULT_PROVIDER = "openrouter"

# Slackトークン
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")

if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
    print("❌ エラー: SLACK_BOT_TOKEN と SLACK_APP_TOKEN を環境変数に設定してください。")
    exit(1)

# クライアント初期化
web_client = WebClient(token=SLACK_BOT_TOKEN)
socket_client = SocketModeClient(
    app_token=SLACK_APP_TOKEN,
    web_client=web_client
)

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
    import re
    cmd_text = re.sub(r'<@U[A-Z0-9]+>\s*', '', text_strip).strip()

    if cmd_text.startswith("/"):
        handle_command(cmd_text, channel, thread_ts, settings)
        return

    # ファイル処理
    files = event.get("files", [])
    attachments = process_slack_files(files)

    # moco API呼び出し
    try:
        logger.info(f"🚀 moco に送信中... User:{user} [{settings['profile']}/{settings['provider']}]")
        
        payload = {
            "message": cmd_text,
            "session_id": settings["session_id"],
            "profile": settings["profile"],
            "provider": settings["provider"]
        }
        if attachments:
            payload["attachments"] = attachments
            if not cmd_text:
                payload["message"] = "この画像について教えてください。"

        with httpx.Client(timeout=300.0) as http:
            resp = http.post(MOCO_API_URL, json=payload)
        
        if resp.status_code == 200:
            data = resp.json()
            reply = data.get("response", "（応答なし）")
            new_session_id = data.get("session_id")
            if new_session_id:
                settings["session_id"] = new_session_id

            # Slackに返信
            web_client.chat_postMessage(
                channel=channel,
                text=reply,
                thread_ts=thread_ts
            )
            logger.info("📤 返信完了")
        else:
            web_client.chat_postMessage(
                channel=channel,
                text=f"❌ moco エラー: {resp.status_code}",
                thread_ts=thread_ts
            )
    except Exception as e:
        logger.error(f"❌ エラー: {e}")
        web_client.chat_postMessage(
            channel=channel,
            text=f"❌ 接続エラー: {e}",
            thread_ts=thread_ts
        )

def handle_command(text: str, channel: str, thread_ts: str, settings: dict):
    parts = text.split()
    cmd = parts[0].lower()
    
    reply = ""
    if cmd in ["/clear", "/new"]:
        settings["session_id"] = None
        reply = "🗑️ セッションをクリアしました"
    elif cmd == "/profile" and len(parts) > 1:
        settings["profile"] = parts[1]
        reply = f"✅ プロファイルを変更: {parts[1]}"
    elif cmd == "/provider" and len(parts) > 1:
        settings["provider"] = parts[1]
        reply = f"✅ プロバイダを変更: {parts[1]}"
    elif cmd == "/status":
        reply = f"📊 現在の設定\nプロファイル: {settings['profile']}\nプロバイダ: {settings['provider']}\nセッション: {settings['session_id'] or '(新規)'}"
    elif cmd == "/help":
        reply = "📚 *moco Slack ヘルプ*\n`/profile <name>` - プロファイル変更\n`/provider <name>` - プロバイダ変更\n`/new` - 新しいセッション\n`/status` - 設定表示"
    else:
        reply = f"❓ 不明なコマンド: {cmd}"

    web_client.chat_postMessage(channel=channel, text=reply, thread_ts=thread_ts)

if __name__ == "__main__":
    # ボットのユーザーIDを取得（ループ防止用）
    auth_test = web_client.auth_test()
    bot_user_id = auth_test["user_id"]
    logger.info(f"🤖 Bot User ID: {bot_user_id}")

    socket_client.socket_mode_request_listeners.append(handle_message)
    
    logger.info("⚡ Socket Mode Client 接続中...")
    socket_client.connect()
    
    from threading import Event
    Event().wait()
