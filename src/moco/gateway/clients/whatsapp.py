#!/usr/bin/env python3
"""
WhatsApp ↔ moco 連携

使い方:
1. moco ui を起動: moco ui
2. このスクリプトを実行: python whatsapp_moco.py
3. WhatsAppでメッセージを送る → mocoが処理 → 結果を返信

対応メディア:
- テキスト
- 画像（自動認識してmocoに送信）
"""

import httpx
import threading
import uuid
from neonize.client import NewClient
from neonize.events import MessageEv, ConnectedEv, QREv, event

# 設定
MOCO_BASE_URL = "http://localhost:8000/api"
MOCO_API_URL = f"{MOCO_BASE_URL}/chat"
DEFAULT_PROFILE = "cursor"
DEFAULT_PROVIDER = "openrouter"
DEFAULT_WORKING_DIR = "."  # モバイルからの作業ディレクトリ（実行時のカレントディレクトリ）

# WhatsApp クライアント
client = NewClient("moco_whatsapp")

# 接続完了フラグ（起動時の過去メッセージを無視するため）
is_connected = False

# ユーザーごとの設定とロック
user_settings = {}  # {sender: {"session_id": str, "profile": str, "provider": str, "working_dir": str, "lock": threading.Lock}}

def get_user_settings(sender: str) -> dict:
    """ユーザー設定を取得（なければデフォルト作成）"""
    if sender not in user_settings:
        # 作業ディレクトリを作成
        import os
        os.makedirs(DEFAULT_WORKING_DIR, exist_ok=True)
        
        user_settings[sender] = {
            "session_id": None,
            "profile": DEFAULT_PROFILE,
            "provider": DEFAULT_PROVIDER,
            "working_dir": DEFAULT_WORKING_DIR,
            "lock": threading.Lock()
        }
    return user_settings[sender]


@client.event(QREv)
def on_qr(c: NewClient, qr: QREv):
    print("\n🔲 QRコードをスキャンしてください:")
    # neonize バージョン互換性
    if hasattr(qr, 'print_qr'):
        qr.print_qr()
    elif hasattr(qr, 'QR'):
        # QRコードを文字列として表示
        try:
            import qrcode
            qr_obj = qrcode.QRCode()
            qr_obj.add_data(qr.QR)
            qr_obj.print_ascii(invert=True)
        except ImportError:
            print(f"QR Code: {qr.QR}")
            print("(qrcode パッケージをインストールするとQRコードが表示されます: pip install qrcode)")
    else:
        print(f"QR Event: {qr}")


@client.event(ConnectedEv)
def on_connected(c: NewClient, ev: ConnectedEv):
    global is_connected
    is_connected = True
    print("\n" + "="*60)
    print("✅ WhatsApp 接続完了！")
    print("📱 メッセージを送ると moco が処理します")
    print("="*60 + "\n")


@client.event(MessageEv)
def on_message(c: NewClient, ev: MessageEv):
    # 接続完了前のメッセージは無視（起動時の履歴同期）
    if not is_connected:
        return
    
    info = ev.Info
    msg = ev.Message
    
    # 自分以外のメッセージは無視
    is_from_me = info.MessageSource.IsFromMe
    if not is_from_me:
        return
    
    # テキスト取得
    text = ""
    
    if msg.conversation:
        text = msg.conversation
    elif msg.extendedTextMessage and msg.extendedTextMessage.text:
        text = msg.extendedTextMessage.text
    elif msg.imageMessage and msg.imageMessage.caption:
        text = msg.imageMessage.caption
    elif msg.documentMessage and msg.documentMessage.caption:
        text = msg.documentMessage.caption
    
    # 自分の返信メッセージは無視（ループ防止）
    if text and (text.startswith("[moco]") or text.startswith("❌")):
        return
    
    # 特殊コマンドを先に処理（画像処理をスキップ）
    if text and text.startswith("/"):
        sender = str(info.MessageSource.Sender)
        settings = get_user_settings(sender)
        text_lower = text.lower().strip()
        
        print(f"\n📩 受信: {text}")
        
        if text_lower == "/clear" or text_lower == "/new":
            settings["session_id"] = None
            client.reply_message("🗑️ セッションをクリアしました", ev)
            print("📤 セッションクリア")
            return
        
        if text_lower.startswith("/profile "):
            new_profile = text[9:].strip()
            if new_profile:
                settings["profile"] = new_profile
                client.reply_message(f"✅ プロファイルを変更: {new_profile}", ev)
                print(f"📤 プロファイル変更: {new_profile}")
            return
        
        if text_lower.startswith("/provider "):
            new_provider = text[10:].strip()
            if new_provider:
                settings["provider"] = new_provider
                client.reply_message(f"✅ プロバイダを変更: {new_provider}", ev)
                print(f"📤 プロバイダ変更: {new_provider}")
            return
        
        if text_lower == "/status":
            status = f"""📊 現在の設定

プロファイル: {settings['profile']}
プロバイダ: {settings['provider']}
作業ディレクトリ: {settings['working_dir']}
セッション: {settings['session_id'] or '(新規)'}"""
            client.reply_message(status, ev)
            return

        if text_lower == "/stop" or text_lower == "/interrupt":
            if settings["session_id"]:
                try:
                    with httpx.Client() as http:
                        resp = http.post(f"{MOCO_BASE_URL}/sessions/{settings['session_id']}/cancel")
                    if resp.status_code == 200:
                        client.reply_message("🛑 実行を中断しました", ev)
                        print(f"📤 中断成功: {settings['session_id']}")
                    else:
                        client.reply_message("❌ 中断に失敗しました（実行中ではない可能性があります）", ev)
                except Exception as e:
                    client.reply_message(f"⚠️ 中断エラー: {e}", ev)
            else:
                client.reply_message("❓ 実行中のタスクがありません", ev)
            return

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
                                reply = f"✅ 作業ディレクトリを変更しました: {data['working_directory']}"
                            else:
                                detail = resp.json().get("detail", "Unknown error")
                                reply = f"❌ 変更に失敗しました: {detail}"
                    except Exception as e:
                        reply = f"⚠️ サーバー通信エラー: {e}"
                else:
                    # セッションがない場合はローカルのみ（検証なし、将来的なセッション開始時に使用）
                    import os
                    abs_path = os.path.abspath(new_dir)
                    settings["working_dir"] = abs_path
                    reply = f"✅ 作業ディレクトリ(ローカル)を変更: {abs_path}"
                            
                client.reply_message(reply, ev)
                print(f"📤 {reply}")
            return
        
        if text_lower == "/workdir" or text_lower == "/cd":
            client.reply_message(f"📁 現在の作業ディレクトリ: {settings['working_dir']}", ev)
            return
        
        if text_lower == "/help":
            help_text = """📚 moco WhatsApp ヘルプ

/profile <名前> - プロファイル変更
/provider <名前> - プロバイダ変更
/workdir <パス> - 作業ディレクトリ変更 (短縮形: /cd)
/new または /clear - 新しいセッション
/stop - 実行中のタスクを中断
/status - 現在の設定を表示
/help - このヘルプを表示

例:
/workdir ./data
/profile development
/provider openrouter
/stop"""
            client.reply_message(help_text, ev)
            return
    
    # 画像・ドキュメントメッセージの処理（ここでは検出のみ、ダウンロードはスレッド内で行う）
    has_image = bool(msg.imageMessage)
    has_doc = bool(msg.documentMessage)
    
    # テキストもメディアもない場合はスキップ
    if not text and not has_image and not has_doc:
        return
    
    print(f"\n📩 受信: {text}" + (" + [画像検出]" if has_image else "") + (" + [ドキュメント検出]" if has_doc else ""))
    
    sender = str(info.MessageSource.Sender)
    settings = get_user_settings(sender)
    
    # mocoに送信 (スレッド化して受信を妨げないようにする)
    def call_moco_thread():
        # 同一ユーザーからの同時リクエストを制御（スレッドセーフなロック）
        # ロック取得に失敗した場合は、後続の要求として待機せず通知して終了
        lock = settings.get("lock")
        if lock and not lock.acquire(blocking=False):
            client.reply_message("⚠️ 前のリクエストを処理中です。しばらくお待ちください。", ev)
            return

        try:
            # 重い I/O 処理（ファイルのダウンロード）をスレッド内で行う
            current_attachments = []
            working_dir = settings.get("working_dir") or DEFAULT_WORKING_DIR

            # 保存先ディレクトリの準備
            import os
            os.makedirs(working_dir, exist_ok=True)

            # 画像の処理
            if msg.imageMessage:
                try:
                    print("🖼️ 画像をダウンロード中...")
                    image_data = c.download_any(msg)
                    if image_data:
                        # ワークスペースに保存
                        file_name = f"image_{uuid.uuid4().hex[:8]}.jpg"
                        file_path = os.path.join(working_dir, file_name)
                        with open(file_path, "wb") as f:
                            f.write(image_data)

                        current_attachments.append({
                            "type": "image",
                            "name": file_name,
                            "path": file_path
                        })
                        print(f"✅ 画像を保存しました: {file_path}")
                except Exception as e:
                    print(f"⚠️ 画像ダウンロードエラー: {e}")

            # ドキュメントの処理
            if msg.documentMessage:
                try:
                    doc = msg.documentMessage
                    file_name = doc.fileName or f"file_{uuid.uuid4().hex[:8]}"
                    print(f"📄 ドキュメントをダウンロード中 ({file_name})...")
                    doc_data = c.download_any(msg)
                    if doc_data:
                        # ワークスペースに保存
                        file_path = os.path.join(working_dir, file_name)
                        with open(file_path, "wb") as f:
                            f.write(doc_data)

                        current_attachments.append({
                            "type": "file",
                            "name": file_name,
                            "path": file_path
                        })
                        print(f"✅ ドキュメントを保存しました: {file_path}")
                except Exception as e:
                    print(f"⚠️ ドキュメントダウンロードエラー: {e}")

            # 処理用テキストの決定
            thread_text = text
            if not thread_text and current_attachments:
                att0 = current_attachments[0]
                if att0["type"] == "image":
                    thread_text = f"画像 {att0['name']} について教えてください。"
                else:
                    thread_text = f"添付ファイル {att0['name']} を解析して内容を説明してください。"

            if not thread_text and not current_attachments:
                return

            client.reply_message("⏳ 処理を開始しました。完了までお待ちください...", ev)

            print(f"🚀 moco に送信中... [{settings['profile']}/{settings['provider']}]" + 
                  (f" (画像{len(current_attachments)}枚含む)" if current_attachments else ""))
            
            payload = {
                "message": thread_text,
                "session_id": settings["session_id"],
                "profile": settings["profile"],
                "provider": settings["provider"],
                "working_directory": settings["working_dir"]
            }
            
            # 添付ファイルがあれば追加
            if current_attachments:
                payload["attachments"] = current_attachments
            
            # タイムアウトを 無制限に設定
            with httpx.Client(timeout=None) as http:
                response = http.post(MOCO_API_URL, json=payload)
            
            if response.status_code == 200:
                data = response.json()
                result = data.get("response", "（応答なし）")
                new_session_id = data.get("session_id")
                
                # セッションを保存
                if new_session_id:
                    settings["session_id"] = new_session_id
                
                # WhatsApp のメッセージ制限（約4000-6000文字）に配慮
                if len(result) > 4000:
                    result = result[:4000] + "\n\n... (長すぎるため省略)"
                
                client.reply_message(result, ev)
                print(f"📤 返信完了 ({len(result)} 文字)")
            else:
                try:
                    error_detail = response.json().get("detail", str(response.status_code))
                except Exception:
                    error_detail = response.text[:100]
                error_msg = f"❌ moco エラー: {error_detail}"
                client.reply_message(error_msg, ev)
                print(error_msg)
                
        except httpx.ConnectError:
            error_msg = "❌ moco に接続できません。moco ui を起動してください。"
            client.reply_message(error_msg, ev)
            print(error_msg)
        except Exception as e:
            error_msg = f"❌ エラー: {e}"
            client.reply_message(error_msg, ev)
            print(error_msg)
        finally:
            if lock:
                lock.release()

    # スレッドを開始
    threading.Thread(target=call_moco_thread, daemon=True).start()


def main():
    print("""
╔════════════════════════════════════════════════════════════════╗
║              WhatsApp ↔ moco 連携                              ║
╠════════════════════════════════════════════════════════════════╣
║  前提: moco ui が起動していること (moco ui)                    ║
║  終了: Ctrl+C                                                  ║
╠════════════════════════════════════════════════════════════════╣
║  コマンド:                                                     ║
║    /workdir <パス>  - 作業ディレクトリ変更 (短縮: /cd)         ║
║    /profile <名前>  - プロファイル変更                         ║
║    /provider <名前> - プロバイダ変更                           ║
║    /stop            - 実行を中断                               ║
║    /new             - 新しいセッション                         ║
║    /status          - 現在の設定を表示                         ║
║    /help            - ヘルプ表示                               ║
╚════════════════════════════════════════════════════════════════╝
    """)
    
    try:
        print("🚀 WhatsApp 接続開始...")
        print("   初回はQRコードが表示されます。スマホでスキャンしてください。")
        print()
        client.connect()
        event.wait()
    except KeyboardInterrupt:
        print("\n👋 終了します...")


if __name__ == "__main__":
    main()
