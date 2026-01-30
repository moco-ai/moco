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
- ドキュメント
"""

from __future__ import annotations
import os
import sqlite3
import subprocess
import time
import httpx
import base64
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Set, List, Any

# === 設定 ===
MOCO_BASE_URL = "http://localhost:8000/api"
MOCO_API_URL = f"{MOCO_BASE_URL}/chat"
DEFAULT_PROFILE = "cursor"
DEFAULT_PROVIDER = "openrouter"
DEFAULT_WORKING_DIR = "."  # モバイルからの作業ディレクトリ（実行時のカレントディレクトリ）

# iMessage データベースパス
CHAT_DB_PATH = Path.home() / "Library/Messages/chat.db"

# ポーリング間隔（秒）
POLL_INTERVAL = 2

# ユーザーごとの設定（セッション、プロファイル、プロバイダ）
user_settings: Dict[str, Dict[str, Any]] = {}

# 処理済みメッセージID
processed_messages: Set[int] = set()


def get_user_settings(sender: str) -> Dict[str, Any]:
    """ユーザー設定を取得（なければデフォルト作成）"""
    if sender not in user_settings:
        # 作業ディレクトリを作成
        os.makedirs(DEFAULT_WORKING_DIR, exist_ok=True)
        
        user_settings[sender] = {
            "session_id": None,
            "profile": DEFAULT_PROFILE,
            "provider": DEFAULT_PROVIDER,
            "model": None,  # None = プロバイダのデフォルトモデルを使用
            "working_dir": DEFAULT_WORKING_DIR,
            "lock": threading.Lock(),
            "active_request_id": None  # リクエストID管理（キャンセル時の復旧用）
        }
    return user_settings[sender]


def get_apple_id() -> Optional[str]:
    """自分のApple ID（電話番号/メールアドレス）を取得"""
    try:
        subprocess.run(
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
            
            file_path = Path(filename)
            if file_path.exists():
                # 画像ファイル
                if mime_type.startswith("image/"):
                    attachments.append({
                        "type": "image",
                        "name": transfer_name,
                        "path": str(file_path),
                        "mime_type": mime_type
                    })
                # その他のファイル
                else:
                    attachments.append({
                        "type": "file",
                        "name": transfer_name,
                        "path": str(file_path),
                        "mime_type": mime_type
                    })
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


def send_imessage_file(recipient: str, file_path: str, caption: str = "") -> bool:
    """
    iMessage でファイルを送信
    
    Args:
        recipient: 電話番号またはメールアドレス
        file_path: 送信するファイルのパス
        caption: キャプション（先にテキストとして送信）
    """
    # キャプションがあれば先に送信
    if caption:
        send_imessage(recipient, caption)
    
    # ファイル送信用 AppleScript
    script = f'''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant "{recipient}" of targetService
        set theFile to POSIX file "{file_path}"
        send theFile to targetBuddy
    end tell
    '''
    
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            return True
        else:
            print(f"❌ ファイル送信 AppleScript エラー: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ ファイル送信タイムアウト")
        return False
    except Exception as e:
        print(f"❌ ファイル送信エラー: {e}")
        return False


def handle_special_commands(text: str, sender: str) -> Optional[str]:
    """特殊コマンドを処理"""
    
    settings = get_user_settings(sender)
    text_lower = text.lower().strip()
    
    if text_lower == "/clear" or text_lower == "/new":
        settings["session_id"] = None
        return "🗑️ セッションをクリアしました"
    
    if text_lower == "/stop" or text_lower == "/interrupt":
        if settings["session_id"]:
            try:
                with httpx.Client() as http:
                    resp = http.post(f"{MOCO_BASE_URL}/sessions/{settings['session_id']}/cancel")
                if resp.status_code == 200:
                    # ローカル状態を強制リセット
                    settings["active_request_id"] = None
                    lock = settings.get("lock")
                    if lock and lock.locked():
                        try:
                            lock.release()
                            print("🔓 ロックを強制解放しました")
                        except RuntimeError:
                            pass
                    return "🛑 実行を中断しました"
                else:
                    return "❌ 中断に失敗しました（実行中ではない可能性があります）"
            except Exception as e:
                return f"⚠️ 中断エラー: {e}"
        else:
            return "❓ 実行中のタスクがありません"
    
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
    
    if text_lower.startswith("/model "):
        new_model = text[7:].strip()
        if new_model:
            settings["model"] = new_model
            return f"✅ モデルを変更: {new_model}"
        return None
    
    if text_lower.startswith("/workdir ") or text_lower.startswith("/cd "):
        new_dir = text.split(" ", 1)[1].strip()
        if new_dir:
            # サーバーにリクエストを投げて、サーバー側で検証させる
            if settings["session_id"]:
                try:
                    with httpx.Client() as http:
                        resp = http.post(
                            f"{MOCO_BASE_URL}/sessions/{settings['session_id']}/workdir",
                            json={"working_directory": new_dir}
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            settings["working_dir"] = data["working_directory"]
                            return f"✅ 作業ディレクトリを変更しました: {data['working_directory']}"
                        else:
                            detail = resp.json().get("detail", "Unknown error")
                            return f"❌ 変更に失敗しました: {detail}"
                except Exception as e:
                    return f"⚠️ サーバー通信エラー: {e}"
            else:
                # セッションがない場合はローカルのみ（検証なし）
                abs_path = os.path.abspath(new_dir)
                settings["working_dir"] = abs_path
                return f"✅ 作業ディレクトリ(ローカル)を変更: {abs_path}"
        return None
    
    if text_lower == "/workdir" or text_lower == "/cd":
        return f"📁 現在の作業ディレクトリ: {settings['working_dir']}"
    
    if text_lower == "/status":
        model_display = settings.get('model') or '(デフォルト)'
        return f"""📊 現在の設定

プロファイル: {settings['profile']}
プロバイダ: {settings['provider']}
モデル: {model_display}
作業ディレクトリ: {settings['working_dir']}
セッション: {settings['session_id'] or '(新規)'}"""
    
    if text_lower == "/help":
        return """📱 iMessage ↔ moco ヘルプ

/profile <名前> - プロファイル変更
/provider <名前> - プロバイダ変更
/model <名前> - モデル変更
/workdir <パス> - 作業ディレクトリ変更 (短縮形: /cd)
/new または /clear - 新しいセッション
/stop - 実行中のタスクを中断
/status - 現在の設定を表示
/help - このヘルプを表示

例:
/provider openrouter
/model x-ai/grok-code-fast-1
/profile development
/workdir ./data"""
    
    return None


def process_moco_request(text: str, sender: str, attachments: Optional[List[dict]] = None):
    """moco APIを呼び出して返信を送信（スレッドセーフ）"""
    
    settings = get_user_settings(sender)
    
    # 同一ユーザーからの同時リクエストを制御
    lock = settings.get("lock")
    if lock and not lock.acquire(blocking=False):
        send_imessage(sender, "⚠️ 前のリクエストを処理中です。しばらくお待ちください。")
        return
    
    # リクエストIDを生成（キャンセル検知用）
    request_id = str(uuid.uuid4())
    settings["active_request_id"] = request_id
    
    try:
        # 処理開始メッセージ
        send_imessage(sender, "⏳ 処理を開始しました。完了までお待ちください...")
        
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] 🚀 moco に送信中... [{settings['profile']}/{settings['provider']}]" + 
              (f" (添付{len(attachments)}件)" if attachments else ""))
        
        payload = {
            "message": text,
            "profile": settings["profile"],
            "provider": settings["provider"],
            "session_id": settings["session_id"],
            "working_directory": settings["working_dir"]
        }
        
        # モデルが指定されていれば追加
        if settings.get("model"):
            payload["model"] = settings["model"]
        
        # 添付ファイルがあれば追加
        if attachments:
            payload["attachments"] = attachments
        
        # タイムアウトを無制限に設定
        response = httpx.post(
            MOCO_API_URL,
            json=payload,
            timeout=None
        )
        
        # キャンセルチェック: リクエストIDが変わっていたら無視
        if settings["active_request_id"] != request_id:
            print(f"⚠️ リクエスト {request_id[:8]} はキャンセルされました（結果を破棄）")
            return
        
        if response.status_code == 200:
            data = response.json()
            result = data.get("response", "（応答なし）")
            new_session_id = data.get("session_id")
            artifacts = data.get("artifacts", [])
            print(f"🔍 APIレスポンス artifacts: {len(artifacts)}件 - {artifacts}")
            
            # セッションIDを保存
            if new_session_id:
                settings["session_id"] = new_session_id
            
            # iMessage のメッセージ制限に配慮
            MAX_LENGTH = 4000
            if len(result) > MAX_LENGTH:
                result = result[:MAX_LENGTH] + "\n\n... (長すぎるため省略)"
            
            # アーティファクト（ツール経由で送信されたファイル）を処理
            artifact_count = 0
            for artifact in artifacts:
                a_path = artifact.get("path")
                a_type = artifact.get("type", "document")
                a_caption = artifact.get("caption", "")
                if a_path and os.path.exists(a_path):
                    try:
                        print(f"📦 アーティファクト送信中: {a_path} ({a_type})")
                        if send_imessage_file(sender, a_path, a_caption):
                            artifact_count += 1
                            print(f"📁 アーティファクト送信完了: {a_path}")
                        else:
                            print(f"❌ アーティファクト送信失敗: {a_path}")
                    except Exception as e:
                        print(f"❌ アーティファクト送信失敗 ({a_path}): {e}")
            
            # テキスト返信
            send_imessage(sender, result)
            print(f"[{timestamp}] 📤 返信完了 ({len(result)} 文字, アーティファクト {artifact_count}件)")
        else:
            try:
                error_detail = response.json().get("detail", str(response.status_code))
            except Exception:
                error_detail = response.text[:100]
            error_msg = f"❌ moco エラー: {error_detail}"
            send_imessage(sender, error_msg)
            print(error_msg)
            
    except httpx.ConnectError:
        error_msg = "❌ moco に接続できません。moco ui を起動してください。"
        send_imessage(sender, error_msg)
        print(error_msg)
    except httpx.TimeoutException:
        error_msg = "❌ moco タイムアウト"
        send_imessage(sender, error_msg)
        print(error_msg)
    except Exception as e:
        error_msg = f"❌ エラー: {e}"
        send_imessage(sender, error_msg)
        print(error_msg)
    finally:
        if lock and lock.locked():
            try:
                lock.release()
            except RuntimeError:
                pass


def main():
    """メインループ"""
    
    print("""
╔════════════════════════════════════════════════════════════════╗
║              iMessage ↔ moco 連携                              ║
╠════════════════════════════════════════════════════════════════╣
║  前提: moco ui が起動していること (moco ui)                    ║
║  終了: Ctrl+C                                                  ║
╠════════════════════════════════════════════════════════════════╣
║  コマンド:                                                     ║
║    /workdir <パス>  - 作業ディレクトリ変更 (短縮: /cd)         ║
║    /profile <名前>  - プロファイル変更                         ║
║    /provider <名前> - プロバイダ変更                           ║
║    /model <名前>    - モデル変更                               ║
║    /stop            - 実行を中断                               ║
║    /new             - 新しいセッション                         ║
║    /status          - 現在の設定を表示                         ║
║    /help            - ヘルプ表示                               ║
╚════════════════════════════════════════════════════════════════╝
    """)
    
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
    
    print("✅ データベース接続OK")
    print(f"🔗 moco API: {MOCO_API_URL}")
    print(f"👤 デフォルトプロファイル: {DEFAULT_PROFILE}")
    print(f"🤖 デフォルトプロバイダ: {DEFAULT_PROVIDER}")
    print()
    print("📨 メッセージを待機中...")
    print("   別のデバイス（iPhone等）から自分のMacにiMessageを送信してください。")
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
                if text and (text.startswith("[moco]") or text.startswith("❌") or 
                            text.startswith("🔄") or text.startswith("📱") or
                            text.startswith("⏳") or text.startswith("🗑️")):
                    continue
                
                timestamp = datetime.now().strftime("%H:%M:%S")
                attachment_info = f" + 📎{len(attachments)}件" if attachments else ""
                print(f"[{timestamp}] 📨 {sender}: {text[:50] if text else '(添付のみ)'}{attachment_info}...")
                
                # 特殊コマンド処理
                if text:
                    special_response = handle_special_commands(text, sender)
                    if special_response:
                        print(f"[{timestamp}] 📤 {special_response[:50]}...")
                        send_imessage(sender, special_response)
                        continue
                
                # 画像/ファイルのみの場合はデフォルトメッセージ
                if not text and attachments:
                    att0 = attachments[0]
                    if att0["type"] == "image":
                        text = f"画像 {att0['name']} について教えてください。"
                    else:
                        text = f"添付ファイル {att0['name']} を解析して内容を説明してください。"
                
                # moco に送信 (スレッド化して受信監視を止めないようにする)
                threading.Thread(
                    target=process_moco_request,
                    args=(text, sender, attachments if attachments else None),
                    daemon=True
                ).start()
            
            time.sleep(POLL_INTERVAL)
            
    except KeyboardInterrupt:
        print("\n\n👋 終了します")


if __name__ == "__main__":
    main()
