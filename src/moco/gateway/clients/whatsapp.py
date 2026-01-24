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
import base64
from neonize.client import NewClient
from neonize.events import MessageEv, ConnectedEv, QREv, event

# 設定
MOCO_API_URL = "http://localhost:8000/api/chat"
DEFAULT_PROFILE = "cursor"
DEFAULT_PROVIDER = "openrouter"
DEFAULT_WORKING_DIR = "/tmp/moco-mobile"  # モバイルからの作業ディレクトリ

# WhatsApp クライアント
client = NewClient("moco_whatsapp")

# 接続完了フラグ（起動時の過去メッセージを無視するため）
is_connected = False

# ユーザーごとの設定（セッション、プロファイル、プロバイダ）
user_settings = {}  # {sender: {"session_id": str, "profile": str, "provider": str, "working_dir": str}}


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
            "working_dir": DEFAULT_WORKING_DIR
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
セッション: {settings['session_id'] or '(新規)'}"""
            client.reply_message(status, ev)
            return
        
        if text_lower == "/help":
            help_text = """📚 moco WhatsApp ヘルプ

/profile <名前> - プロファイル変更
/provider <名前> - プロバイダ変更
/new または /clear - 新しいセッション
/status - 現在の設定を表示
/help - このヘルプを表示

例:
/profile development
/provider gemini"""
            client.reply_message(help_text, ev)
            return
    
    # 画像メッセージの処理
    attachments = []
    if msg.imageMessage:
        try:
            print("🖼️ 画像を検出...")
            # 画像をダウンロード
            image_data = c.download_any(msg)
            if image_data:
                # Base64エンコード
                b64_data = base64.b64encode(image_data).decode("utf-8")
                mime_type = msg.imageMessage.mimetype or "image/jpeg"
                attachments.append({
                    "type": "image",
                    "name": "whatsapp_image.jpg",
                    "mime_type": mime_type,
                    "data": b64_data
                })
                print(f"✅ 画像取得完了 ({len(image_data)} bytes)")
                # キャプションがなければデフォルト
                if not text:
                    text = "この画像について教えてください。"
        except Exception as e:
            print(f"⚠️ 画像取得エラー: {e}")
    
    # テキストも画像もない場合はスキップ
    if not text and not attachments:
        return
    
    print(f"\n📩 受信: {text}" + (f" + {len(attachments)}個の添付" if attachments else ""))
    
    sender = str(info.MessageSource.Sender)
    settings = get_user_settings(sender)
    
    # mocoに送信
    try:
        print(f"🚀 moco に送信中... [{settings['profile']}/{settings['provider']}]" + 
              (f" (画像{len(attachments)}枚含む)" if attachments else ""))
        
        payload = {
            "message": text,
            "session_id": settings["session_id"],
            "profile": settings["profile"],
            "provider": settings["provider"],
            "working_directory": settings["working_dir"]
        }
        
        # 添付ファイルがあれば追加
        if attachments:
            payload["attachments"] = attachments
        
        with httpx.Client(timeout=300.0) as http:
            response = http.post(MOCO_API_URL, json=payload)
        
        if response.status_code == 200:
            data = response.json()
            result = data.get("response", "（応答なし）")
            new_session_id = data.get("session_id")
            
            # セッションを保存
            if new_session_id:
                settings["session_id"] = new_session_id
            
            # 長すぎる場合は切り詰め
            if len(result) > 4000:
                result = result[:4000] + "\n\n... (長すぎるため省略)"
            
            client.reply_message(result, ev)
            print(f"📤 返信完了 ({len(result)} 文字)")
        else:
            error_msg = f"❌ moco エラー: {response.status_code}"
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


def main():
    print("""
╔════════════════════════════════════════════════════════════════╗
║              WhatsApp ↔ moco 連携                              ║
╠════════════════════════════════════════════════════════════════╣
║  前提: moco ui が起動していること (moco ui)                    ║
║  終了: Ctrl+C                                                  ║
╠════════════════════════════════════════════════════════════════╣
║  コマンド:                                                     ║
║    /profile <名前>  - プロファイル変更                         ║
║    /provider <名前> - プロバイダ変更                           ║
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
