# Gateway Clients

外部メッセージングサービスと moco を連携するクライアント。

## 前提条件

- `moco ui` が起動していること
- 各クライアントの依存関係がインストールされていること

## WhatsApp

### インストール

```bash
# Python パッケージ
pip install neonize python-magic

# または moco の mobile オプション
pip install -e ".[mobile]"

# libmagic (OS依存)
brew install libmagic          # macOS
sudo apt install libmagic1     # Ubuntu/Debian
sudo yum install file-libs     # CentOS/RHEL
```

### 起動

```bash
python -m moco.gateway.clients.whatsapp
```

### 初回セットアップ

1. 起動するとQRコードがターミナルに表示される
2. スマホのWhatsAppを開く
3. **設定** → **リンク済みデバイス** → **デバイスをリンク**
4. QRコードをスキャン
5. 「✅ WhatsApp 接続完了！」と表示されたら成功

※ 一度認証すると、次回以降は自動接続されます。

### コマンド

| コマンド | 説明 |
|----------|------|
| `/profile <名前>` | プロファイル変更（例: `/profile development`）|
| `/provider <名前>` | プロバイダ変更（例: `/provider gemini`）|
| `/new` | 新しいセッション開始 |
| `/status` | 現在の設定を表示 |
| `/help` | ヘルプ表示 |

### 画像対応

画像を送信すると、moco が `analyze_image` ツールで分析して返信します。

---

## iMessage

### 前提条件

- macOS
- iMessage が有効
- **フルディスクアクセス** が許可されていること

### フルディスクアクセスの設定

1. **システム設定** → **プライバシーとセキュリティ** → **フルディスクアクセス**
2. **+** をクリック
3. **ターミナル**（または使用するアプリ）を追加

### 起動

```bash
python -m moco.gateway.clients.imessage
```

### 使い方

1. 別のデバイス（iPhone等）から自分のMacにiMessageを送信
2. moco が返信

### コマンド

WhatsAppと同じコマンドが使えます。

---

## 設定

デフォルト設定（各クライアントの先頭で変更可能）:

```python
MOCO_API_URL = "http://localhost:8000/api/chat"
DEFAULT_PROFILE = "cursor"
DEFAULT_PROVIDER = "openrouter"
```

---

## トラブルシューティング

### 「moco に接続できません」

`moco ui` が起動しているか確認:

```bash
moco ui
```

### WhatsApp「QRコードが表示されない」

セッションファイルを削除して再起動:

```bash
rm -rf moco_whatsapp.db
python -m moco.gateway.clients.whatsapp
```

### iMessage「データベースにアクセスできません」

フルディスクアクセスを許可してください（上記参照）。
