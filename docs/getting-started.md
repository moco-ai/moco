# Getting Started

このガイドでは、moco のインストールから最初のエージェント実行までを説明します。

---

## 前提条件

- **Python 3.10 以上**
- **LLM API キー**（以下のいずれか）
    - Gemini API Key
    - OpenAI API Key
    - OpenRouter API Key

---

## インストール

### pip でインストール

```bash
pip install moco-agent
```

### 開発版をインストール

```bash
git clone https://github.com/moco-team/moco-agent.git
cd moco-agent
pip install -e ".[dev]"
```

---

## 環境変数の設定

moco は環境変数で LLM プロバイダを切り替えます。

### Gemini を使用する場合（デフォルト）

```bash
export GEMINI_API_KEY="your-gemini-api-key"
```

!!! tip "API キーの取得"
    Gemini API キーは [Google AI Studio](https://aistudio.google.com/) から取得できます。

### OpenAI を使用する場合

```bash
export LLM_PROVIDER="openai"
export OPENAI_API_KEY="your-openai-api-key"
export OPENAI_MODEL="gpt-4o"  # オプション
```

### OpenRouter を使用する場合

```bash
export LLM_PROVIDER="openrouter"
export OPENROUTER_API_KEY="your-openrouter-api-key"
export OPENROUTER_MODEL="anthropic/claude-3.5-sonnet"  # オプション
```

### .env ファイルを使用

プロジェクトルートに `.env` ファイルを作成することもできます：

```bash title=".env"
GEMINI_API_KEY=your-api-key
# LLM_PROVIDER=openai
# OPENAI_API_KEY=your-openai-key
```

---

## 基本的な使い方

### CLI で対話モード

```bash
# デフォルトプロファイルで起動
moco chat

# プロファイルを指定
moco chat --profile development

# モデルを指定
moco chat --model gemini-2.0-flash
```

### 単発実行

```bash
moco run "このディレクトリの構造を説明してください"
```

### Python API

```python
from moco.core import Orchestrator

# オーケストレーターを作成
orchestrator = Orchestrator(
    profile="default",      # プロファイル名
    provider="gemini",      # LLM プロバイダ（省略時は環境変数）
    stream=True,            # ストリーミング出力
    verbose=False           # 詳細ログ
)

# セッションを作成
session_id = orchestrator.create_session(title="初めてのセッション")

# メッセージを処理
response = orchestrator.process_message(
    "Hello, moco!",
    session_id=session_id
)
print(response)
```

---

## プロファイルの選択

moco は用途別のプロファイルを提供しています：

| プロファイル | 説明 | 用途 |
|-------------|------|------|
| `default` | 汎用（最小構成） | 一般的なタスク |
| `development` | 開発支援 | コーディング、レビュー |
| `security` | セキュリティ分析 | 脆弱性調査、インシデント対応 |
| `tax` | 税務計算 | 税金シミュレーション |

```bash
# 開発プロファイルで起動
moco chat --profile development

# セキュリティプロファイルで起動
moco chat --profile security
```

カスタムプロファイルの作成方法は [プロファイル作成ガイド](guides/profiles.md) を参照してください。

---

## ツールの使用

moco はエージェントに様々なツールを提供します。

### 利用可能なベースツール

| ツール | 説明 |
|--------|------|
| `read_file` | ファイル読み込み |
| `write_file` | ファイル書き込み |
| `edit_file` | ファイル部分編集 |
| `execute_bash` | Bash コマンド実行 |
| `list_dir` | ディレクトリ一覧 |
| `glob_search` | パターン検索 |
| `grep` | テキスト検索 |
| `websearch` | Web 検索 |
| `webfetch` | Web ページ取得 |
| `todowrite` | TODO 作成 |
| `todoread` | TODO 読み込み |

### ツール使用例

エージェントは自動的に適切なツールを選択して使用します：

```
You: プロジェクトの README.md を読んで要約してください

Agent: 📖 read_file → README.md
       README.md の内容を読み込みました。
       
       このプロジェクトは...（要約）
```

---

## セッション管理

moco は会話履歴をセッション単位で管理します。

### セッションの作成と継続

```python
from moco.core import Orchestrator

orchestrator = Orchestrator()

# 新しいセッションを作成
session_id = orchestrator.create_session(title="機能開発")

# メッセージを送信（履歴が保存される）
response1 = orchestrator.process_message("タスクAを実行", session_id)
response2 = orchestrator.process_message("続けてタスクBを実行", session_id)

# 後で同じセッションを継続
orchestrator.continue_session(session_id)
response3 = orchestrator.process_message("前回の続きを教えて", session_id)
```

### CLI でのセッション管理

```bash
# セッション一覧
moco sessions

# 特定のセッションを継続
moco chat --session <session-id>
```

---

## 次のステップ

- [プロファイル作成ガイド](guides/profiles.md) - カスタムプロファイルの作成
- [ガードレール設定](guides/guardrails.md) - セキュリティ設定
- [Core API リファレンス](api/core.md) - 詳細な API ドキュメント
- [Tools API リファレンス](api/tools.md) - ツールの詳細

---

## トラブルシューティング

### API キーエラー

```
ValueError: GEMINI_API_KEY environment variable not set
```

→ 環境変数が正しく設定されているか確認してください。

### モジュールが見つからない

```
ModuleNotFoundError: No module named 'moco'
```

→ `pip install moco-agent` を実行してください。

### プロファイルが見つからない

```
Error: Profile 'xxx' not found
```

→ `moco/profiles/` ディレクトリに該当プロファイルが存在するか確認してください。
