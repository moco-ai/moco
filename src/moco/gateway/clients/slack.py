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
import re
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


def filter_response_for_display(response: str) -> str:
    """レスポンスをフィルタリング（中間出力を除外し、最終結果のみ表示）"""
    if not response:
        return ""
    
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
            return f"{last_agent}: {last_content}"
    
    # "## 完了" セクションを探して、その後の内容を返す
    if "## 完了" in response:
        parts = response.split("## 完了")
        if len(parts) > 1:
            final_part = parts[-1].strip()
            # "## 完了" の後の内容を返す（空なら "## 完了" を含む最後のパート）
            if final_part:
                return final_part
            # 最後のパートが空なら、## 完了 の直前のセクションから有用な内容を探す
            for part in reversed(parts[:-1]):
                # "## 作業内容" を除いた最後の有意義なセクション
                lines = [l for l in part.strip().split("\n") if l.strip() and not l.strip().startswith("## 作業内容")]
                if lines:
                    return "\n".join(lines[-10:])  # 最後の10行を返す
    
    # "## 作業内容" で始まる行を除外
    lines = response.split("\n")
    filtered_lines = []
    skip_section = False
    for line in lines:
        if line.strip().startswith("## 作業内容"):
            skip_section = True
            continue
        elif line.strip().startswith("## ") and not line.strip().startswith("## 作業内容"):
            skip_section = False
        
        if not skip_section:
            filtered_lines.append(line)
    
    result = "\n".join(filtered_lines).strip()
    return result if result else response

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
# ストリーミングではなく通常のAPIを使用（WhatsAppと同じ）
MOCO_API_URL = f"{MOCO_API_BASE}/api/chat"
DEFAULT_PROFILE = "cursor"
DEFAULT_PROVIDER = "openrouter"

# Slackトークン
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")

# グローバル変数（main()内で初期化）
web_client: Optional[WebClient] = None
socket_client: Optional[SocketModeClient] = None


def split_text_for_slack(text: str, limit: int = 1000) -> List[str]:
    """
    Slackに適したサイズにテキストを分割。
    UTF-8マルチバイト文字（日本語など）を考慮して小さめに分割。
    """
    if text is None:
        return []
    
    s = str(text)
    if not s:
        return []
    
    if len(s) <= limit:
        return [s]
    
    chunks: List[str] = []
    i = 0
    n = len(s)
    while i < n:
        remaining = n - i
        if remaining <= limit:
            chunk = s[i:n]
            if chunk:
                chunks.append(chunk)
            break
        
        window = s[i:i + limit]
        cut = window.rfind("\n")
        if cut <= 0:
            # 改行がない場合はハードカット
            chunk = window
            i += limit
        else:
            chunk = window[:cut]
            i += cut + 1  # 改行をスキップ
        
        if chunk:
            chunks.append(chunk)
    
    return chunks


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
    cmd_text = re.sub(r'<@U[A-Z0-9]+>\s*', '', text_strip).strip()

    if cmd_text.startswith("/"):
        handle_command(cmd_text, channel, thread_ts, settings)
        return

    # ファイル処理
    files = event.get("files", [])
    attachments = process_slack_files(files)

    # moco API呼び出し（WhatsAppと同じ非ストリーミング方式）
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

    # バックグラウンドでAPI呼び出し
    def run_api_call():
        try:
            # 処理中メッセージを投稿
            processing_msg = web_client.chat_postMessage(
                channel=channel,
                text="⏳ 処理中...",
                thread_ts=thread_ts
            )
            processing_ts = processing_msg.get("ts")
            
            # タイムアウトなしでAPI呼び出し（WhatsAppと同じ）
            with httpx.Client(timeout=None) as http:
                response = http.post(MOCO_API_URL, json=payload)
            
            if response.status_code == 200:
                data = response.json()
                result = data.get("response", "（応答なし）")
                new_session_id = data.get("session_id")
                
                # セッションID更新
                if new_session_id:
                    settings["session_id"] = new_session_id
                
                # レスポンスをフィルタリング
                filtered_result = filter_response_for_display(result)
                
                # 結果を分割して投稿
                chunks = split_text_for_slack(filtered_result, limit=1000)
                
                if chunks:
                    # 処理中メッセージを最初のチャンクで更新
                    try:
                        web_client.chat_update(
                            channel=channel,
                            ts=processing_ts,
                            text=chunks[0]
                        )
                    except Exception as e:
                        logger.error(f"⚠️ メッセージ更新エラー: {e}")
                    
                    # 残りのチャンクを投稿
                    for chunk in chunks[1:]:
                        time.sleep(1.0)  # レート制限回避
                        try:
                            web_client.chat_postMessage(
                                channel=channel,
                                text=chunk,
                                thread_ts=thread_ts
                            )
                        except Exception as e:
                            logger.error(f"⚠️ チャンク投稿エラー: {e}")
                else:
                    # 結果が空の場合
                    web_client.chat_update(
                        channel=channel,
                        ts=processing_ts,
                        text="（応答なし）"
                    )
                
                logger.info("📤 完了")
            else:
                error_msg = f"❌ moco エラー: {response.status_code}"
                web_client.chat_update(
                    channel=channel,
                    ts=processing_ts,
                    text=error_msg
                )
                logger.error(error_msg)
                
        except httpx.ConnectError:
            error_msg = "❌ moco APIに接続できません"
            web_client.chat_postMessage(channel=channel, text=error_msg, thread_ts=thread_ts)
            logger.error(error_msg)
        except Exception as e:
            error_msg = f"❌ エラー: {e}"
            web_client.chat_postMessage(channel=channel, text=error_msg, thread_ts=thread_ts)
            logger.error(error_msg)
    
    thread = threading.Thread(target=run_api_call)
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
    logger.info(f"📡 moco API: {MOCO_API_URL}")

    socket_client.socket_mode_request_listeners.append(handle_message)
    
    logger.info("⚡ Socket Mode Client 接続中...")
    socket_client.connect()
    
    from threading import Event
    Event().wait()


if __name__ == "__main__":
    main()
