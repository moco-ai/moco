# MOCO

> **M**ulti-agent **O**rchestration **CO**re

**マルチプロバイダ対応・プロファイルベースの軽量AIエージェントオーケストレーションフレームワーク**

MOCO は、複数のLLMプロバイダ（Gemini, OpenAI, OpenRouter, Z.ai）に対応し、ドメイン別のプロファイルで複数エージェントの振る舞いを柔軟にカスタマイズできるマルチエージェントオーケストレーションフレームワークです。

---

## ✨ 特徴

### 主要機能

- 🔄 **マルチプロバイダ対応**: Gemini, OpenAI, OpenRouter, Z.ai を環境変数またはCLIオプションで切り替え
- 📦 **プロファイル機能**: ドメイン別（開発、セキュリティ、税務など）にエージェントとツールをパッケージ化
- 🧠 **セマンティックメモリ**: FAISS による類似度検索で過去の知識・インシデントを自動想起
- 📝 **自動コンテキスト圧縮**: トークン上限に近づくと古い会話を自動要約して圧縮
- 🛡️ **ガードレール**: 危険なコマンドのブロック、入出力長制限、カスタムバリデーション
- 🔌 **MCP対応**: Model Context Protocol で外部ツールサーバーと連携
- 💾 **チェックポイント**: 会話状態を保存し、後から復元可能

### 他のSDKとの比較

| 機能 | moco | Claude Agent SDK | OpenAI Agents SDK |
|------|------|------------------|-------------------|
| **マルチプロバイダ** | ✅ Gemini/OpenAI/OpenRouter/Z.ai | ❌ Claude only | ❌ OpenAI only |
| **プロファイル機能** | ✅ YAML定義でドメイン別設定 | ❌ | ❌ |
| **セマンティックメモリ** | ✅ FAISS + 埋め込み検索 | ❌ | ❌ |
| **自動コンテキスト圧縮** | ✅ トークン上限時に自動要約 | ❌ | ❌ |
| **ガードレール** | ✅ 入力/出力/ツール検証 | ❌ | ✅ |
| **MCP対応** | ✅ Model Context Protocol | ✅ | ❌ |
| **チェックポイント** | ✅ 会話状態の保存/復元 | ❌ | ❌ |

---

## 🚀 クイックスタート

### インストール

```bash
pip install moco-agent
```

### 環境変数の設定

```bash
export GEMINI_API_KEY="your-api-key"
```

### 最初の実行

```bash
# タスクを実行
moco run "Hello, World! と表示するPythonスクリプトを作成して"

# 対話モード
moco chat
```

詳細は [Getting Started](getting-started.md) を参照してください。

---

## 📚 ドキュメント

- [Getting Started](getting-started.md) - インストールと最初の実行
- [プロファイル作成ガイド](profiles.md) - カスタムプロファイルの作成
- [カスタムツール作成ガイド](custom-tools.md) - プロファイル固有ツールの作成
- [Core API リファレンス](core.md) - Orchestrator API
- [Tools API リファレンス](tools.md) - ツール一覧

---

## 📋 CLI コマンド

```bash
# 基本コマンド
moco run "タスク"                    # タスクを実行
moco run "タスク" --model gpt-4o     # モデル指定
moco chat                            # 対話型チャット
moco list-profiles                   # プロファイル一覧

# タスク管理（バックグラウンド実行）
moco tasks run "タスク" -p cursor    # バックグラウンド実行
moco tasks status                    # リアルタイムダッシュボード
moco tasks logs <task_id>            # ログ表示

# セッション管理
moco sessions list                   # セッション一覧
moco run "続き" --continue           # 直前のセッションを継続

# スキル管理
moco skills sync anthropics          # 公式スキルを同期
moco skills search pdf               # スキル検索
```

---

## 📄 ライセンス

MIT License - 詳細は [LICENSE](https://github.com/moco-team/moco-agent/blob/main/LICENSE) を参照
