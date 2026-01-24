#!/usr/bin/env python3
"""
iMessage ↔ moco 連携スクリプト

macOS の iMessage を監視し、受信メッセージを moco に転送して返信します。

必要な設定:
1. システム設定 → プライバシーとセキュリティ → フルディスクアクセス
   → ターミナル（または使用するアプリ）を追加

使い方:
    source venv/bin/activate
    python imessage_moco.py

対応メディア:
- テキスト
- 画像（自動認識してmocoに送信）
"""

from __future__ import annotations
import os
import sqlite3
import subprocess
import time
import httpx
import base64
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Set, List

# === 設定 ===
MOCO_API_URL = "http://localhost:8000/api/chat"
DEFAULT_PROFILE = "cursor"
DEFAULT_PROVIDER = "openrouter"

# iMessage データベースパス
CHAT_DB_PATH = Path.home() / "Library/Messages/chat.db"

# ポーリング間隔（秒）
POLL_INTERVAL = 2

# ユーザーごとの設定（セッション、プロファイル、プロバイダ）
user_settings: Dict[str, Dict[str, Optional[str]]] = {}

# 処理済みメッセージID
processed_messages: Set[int] = set()


def get_user_settings(sender: str) -> Dict[str, Optional[str]]:
    """ユーザー設定を取得（なければデフォルト作成）"""
    if sender not in user_settings:
        user_settings[sender] = {
            "session_id": None,
            "profile": DEFAULT_PROFILE,
            "provider": DEFAULT_PROVIDER
        }
    return user_settings[sender]


def get_apple_id() -> Optional[str]:
    """自分のApple ID（電話番号/メールアドレス）を取得"""
    try:
        result = subprocess.run(
            ["defaults", "read", "com.apple.iChat", "Accounts"],
            capture_output=True,
            text=True
        )
        # 簡易的に取得（完全な実装には追加のパースが必要）
        return None
    except Exception:
        return None


def get_attachments_for_message(conn, message_rowid: int) -> List[dict]:
    """メッセージの添付ファイルを取得"""
    attachments = []
    try:
        cursor = conn.cursor()
        query = """
        SELECT 
            a.filename,
            a.mime_type,
            a.transfer_name
        FROM attachment a
        JOIN message_attachment_join maj ON a.ROWID = maj.attachment_id
        WHERE maj.message_id = ?
        """
        cursor.execute(query, (message_rowid,))
        
        for row in cursor.fetchall():
            filename = row[0]
            mime_type = row[1] or ""
            transfer_name = row[2] or "attachment"
            
            if not filename:
                continue
            
            # ~/Library パスを展開
            if filename.startswith("~"):
                filename = os.path.expanduser(filename)
            
            # 画像ファイルのみ処理
            if not mime_type.startswith("image/"):
                continue
            
            file_path = Path(filename)
            if file_path.exists():
                try:
                    with open(file_path, "rb") as f:
                        data = f.read()
                    b64_data = base64.b64encode(data).decode("utf-8")
                    attachments.append({
                        "type": "image",
                        "name": transfer_name,
                        "mime_type": mime_type,
                        "data": b64_data
                    })
                except Exception as e:
                    print(f"⚠️ 添付ファイル読み込みエラー: {e}")
    except Exception as e:
        print(f"⚠️ 添付取得エラー: {e}")
    
    return attachments


def get_new_messages(last_rowid: int) -> List[dict]:
    """
    新しいメッセージを取得
    
    Returns:
        list of {rowid, text, sender, is_from_me, date, attachments}
    """
    messages = []
    
    try:
        # データベースをコピーして読み取り（ロック回避）
        conn = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 新しいメッセージを取得（テキストがなくても添付ファイルがあれば対象）
        query = """
        SELECT 
            m.ROWID as rowid,
            m.text,
            m.is_from_me,
            m.date,
            h.id as sender,
            m.cache_has_attachments
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        WHERE m.ROWID > ?
            AND (m.text IS NOT NULL OR m.cache_has_attachments = 1)
        ORDER BY m.ROWID ASC
        """
        
        cursor.execute(query, (last_rowid,))
        
        for row in cursor.fetchall():
            rowid = row["rowid"]
            has_attachments = bool(row["cache_has_attachments"])
            
            # 添付ファイルを取得
            attachments = []
            if has_attachments:
                attachments = get_attachments_for_message(conn, rowid)
            
            # テキストも添付もない場合はスキップ
            text = row["text"] or ""
            if not text and not attachments:
                continue
            
            messages.append({
                "rowid": rowid,
                "text": text,
                "sender": row["sender"] or "unknown",
                "is_from_me": bool(row["is_from_me"]),
                "date": row["date"],
                "attachments": attachments
            })
        
        conn.close()
        
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            print("⚠️  データベースがロック中、次回リトライ...")
        else:
            print(f"❌ DB エラー: {e}")
    except Exception as e:
        print(f"❌ エラー: {e}")
    
    return messages


def get_latest_rowid() -> int:
    """最新のメッセージROWIDを取得"""
    try:
        conn = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(ROWID) FROM message")
        result = cursor.fetchone()[0]
        conn.close()
        return result or 0
    except Exception as e:
        print(f"❌ ROWID取得エラー: {e}")
        return 0


def send_imessage(recipient: str, message: str) -> bool:
    """
    iMessage でメッセージを送信
    
    Args:
        recipient: 電話番号またはメールアドレス
        message: 送信するメッセージ
    """
    # AppleScript でメッセージ送信
    # エスケープ処理
    escaped_message = message.replace('\\', '\\\\').replace('"', '\\"')
    
    script = f'''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant "{recipient}" of targetService
        send "{escaped_message}" to targetBuddy
    end tell
    '''
    
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            return True
        else:
            print(f"❌ AppleScript エラー: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ 送信タイムアウト")
        return False
    except Exception as e:
        print(f"❌ 送信エラー: {e}")
        return False


def call_moco(message: str, sender: str, attachments: Optional[List[dict]] = None) -> str:
    """moco API を呼び出し"""
    
    settings = get_user_settings(sender)
    
    payload = {
        "message": message,
        "profile": settings["profile"],
        "provider": settings["provider"],
        "session_id": settings["session_id"]
    }
    
    # 添付ファイルがあれば追加
    if attachments:
        payload["attachments"] = attachments
    
    try:
        response = httpx.post(
            MOCO_API_URL,
            json=payload,
            timeout=120.0
        )
        
        if response.status_code == 200:
            data = response.json()
            # セッションIDを保存
            if "session_id" in data:
                settings["session_id"] = data["session_id"]
            return data.get("response", "（応答なし）")
        else:
            return f"❌ moco エラー: {response.status_code}"
            
    except httpx.TimeoutException:
        return "❌ moco タイムアウト"
    except Exception as e:
        return f"❌ エラー: {e}"


def handle_special_commands(text: str, sender: str) -> Optional[str]:
    """特殊コマンドを処理"""
    
    settings = get_user_settings(sender)
    text_lower = text.lower().strip()
    
    if text_lower == "/clear" or text_lower == "/new":
        settings["session_id"] = None
        return "🔄 セッションをクリアしました"
    
    if text_lower.startswith("/profile "):
        new_profile = text[9:].strip()
        if new_profile:
            settings["profile"] = new_profile
            return f"✅ プロファイルを変更: {new_profile}"
        return None
    
    if text_lower.startswith("/provider "):
        new_provider = text[10:].strip()
        if new_provider:
            settings["provider"] = new_provider
            return f"✅ プロバイダを変更: {new_provider}"
        return None
    
    if text_lower == "/status":
        return f"""📊 現在の設定

プロファイル: {settings['profile']}
プロバイダ: {settings['provider']}
セッション: {settings['session_id'] or '(新規)'}"""
    
    if text_lower == "/help":
        return """📱 iMessage ↔ moco

コマンド:
• /profile <名前> - プロファイル変更
• /provider <名前> - プロバイダ変更
• /new または /clear - 新しいセッション
• /status - 現在の設定を表示
• /help - このヘルプを表示

例:
• /profile development
• /provider gemini"""
    
    return None


def main():
    """メインループ"""
    
    print("=" * 50)
    print("📱 iMessage ↔ moco 連携")
    print("=" * 50)
    
    # データベース確認
    if not CHAT_DB_PATH.exists():
        print(f"❌ iMessage データベースが見つかりません: {CHAT_DB_PATH}")
        print("   iMessage を有効にしてください。")
        return
    
    # フルディスクアクセス確認
    try:
        conn = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True)
        conn.close()
    except sqlite3.OperationalError as e:
        if "unable to open database file" in str(e):
            print("❌ データベースにアクセスできません")
            print("   システム設定 → プライバシーとセキュリティ → フルディスクアクセス")
            print("   → ターミナル（または使用するアプリ）を追加してください")
            return
        raise
    
    print(f"✅ データベース接続OK: {CHAT_DB_PATH}")
    print(f"🔗 moco API: {MOCO_API_URL}")
    print(f"👤 プロファイル: {MOCO_PROFILE}")
    print(f"🤖 プロバイダ: {MOCO_PROVIDER}")
    print("-" * 50)
    print("📨 メッセージを待機中... (Ctrl+C で終了)")
    print()
    
    # 現在の最新ROWIDを取得（過去のメッセージは処理しない）
    last_rowid = get_latest_rowid()
    print(f"📍 開始位置: ROWID={last_rowid}")
    
    try:
        while True:
            # 新しいメッセージを取得
            new_messages = get_new_messages(last_rowid)
            
            for msg in new_messages:
                rowid = msg["rowid"]
                text = msg["text"] or ""
                sender = msg["sender"]
                is_from_me = msg["is_from_me"]
                attachments = msg.get("attachments", [])
                
                # ROWIDを更新
                if rowid > last_rowid:
                    last_rowid = rowid
                
                # 処理済みはスキップ
                if rowid in processed_messages:
                    continue
                processed_messages.add(rowid)
                
                # 自分が送ったメッセージは無視
                if is_from_me:
                    continue
                
                # 自分の返信メッセージは無視（ループ防止）
                if text and (text.startswith("[moco]") or text.startswith("❌") or text.startswith("🔄") or text.startswith("📱")):
                    continue
                
                timestamp = datetime.now().strftime("%H:%M:%S")
                attachment_info = f" + 🖼️{len(attachments)}枚" if attachments else ""
                print(f"[{timestamp}] 📨 {sender}: {text[:50] if text else '(画像のみ)'}{attachment_info}...")
                
                # 特殊コマンド処理
                if text:
                    special_response = handle_special_commands(text, sender)
                    if special_response:
                        print(f"[{timestamp}] 📤 {special_response[:50]}...")
                        send_imessage(sender, special_response)
                        continue
                
                # 画像のみの場合はデフォルトメッセージ
                if not text and attachments:
                    text = "この画像について教えてください。"
                
                # moco に送信
                response = call_moco(text, sender, attachments if attachments else None)
                
                # 返信
                reply = f"[moco] {response}"
                
                # 長すぎる場合は分割
                MAX_LENGTH = 1000
                if len(reply) > MAX_LENGTH:
                    reply = reply[:MAX_LENGTH] + "..."
                
                print(f"[{timestamp}] 📤 返信: {reply[:50]}...")
                send_imessage(sender, reply)
            
            time.sleep(POLL_INTERVAL)
            
    except KeyboardInterrupt:
        print("\n\n👋 終了します")


if __name__ == "__main__":
    main()
