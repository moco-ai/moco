# プロファイル作成ガイド

プロファイルは、特定のドメインや用途に特化したエージェントとツールのセットです。

---

## ディレクトリ構造

```
profiles/<profile-name>/
├── profile.yaml          # プロファイル設定（必須）
├── agents/
│   ├── orchestrator.md   # メインエージェント（必須）
│   ├── coder.md          # サブエージェント（任意）
│   └── reviewer.md       # サブエージェント（任意）
└── tools/
    ├── my_tool.py        # カスタムツール（任意）
    └── another_tool.py
```

---

## profile.yaml

プロファイルのメタデータと設定を定義します。

```yaml
name: my-profile
description: プロファイルの説明
include_base_tools: true  # ベースツール（read_file等）を含めるか

# オプション: MCP サーバー設定
mcp:
  servers:
    - name: filesystem
      command: npx
      args: ["-y", "@anthropic/mcp-server-filesystem", "/path/to/dir"]
```

### フィールド

| フィールド | 必須 | 説明 |
|------------|------|------|
| `name` | ✅ | プロファイル名（ディレクトリ名と一致推奨） |
| `description` | ❌ | プロファイルの説明 |
| `include_base_tools` | ❌ | ベースツールを含めるか（デフォルト: `true`） |
| `mcp` | ❌ | MCP サーバー設定 |

---

## agents/*.md

エージェント定義は Markdown + YAML フロントマターで記述します。

### 基本構造

```markdown
---
description: エージェントの説明（短い説明）
mode: primary
tools:
  read_file: true
  write_file: true
  execute_bash: true
  my_custom_tool: true
---

あなたは優秀なソフトウェアエンジニアです。

## 役割

- コードの作成と修正
- テストの実行

## ルール

1. 必ずテストを書く
2. エラーハンドリングを忘れない

現在時刻: {{CURRENT_DATETIME}}
作業ディレクトリ: {{WORKING_DIRECTORY}}
```

### フロントマター

| フィールド | 必須 | 説明 |
|------------|------|------|
| `description` | ✅ | エージェントの短い説明 |
| `mode` | ❌ | `primary`（デフォルト）または `chat` |
| `tools` | ✅ | 使用可能なツールの辞書 |

### ツール指定

```yaml
tools:
  # ベースツール
  read_file: true
  write_file: true
  edit_file: true
  execute_bash: true
  list_dir: true
  glob_search: true
  tree: true
  file_info: true
  grep: true
  ripgrep: true
  find_definition: true
  find_references: true
  codebase_search: true
  websearch: true
  webfetch: true
  todowrite: true
  todoread: true
  
  # プロセス管理
  start_background: true
  stop_process: true
  list_processes: true
  send_input: true
  wait_for_pattern: true
  
  # Git ツール
  git_status: true
  git_diff: true
  git_commit: true
  create_pr: true
  
  # スキル管理
  search_skills: true
  load_skill: true
  list_loaded_skills: true
  
  # 委譲
  delegate_to_agent: true  # サブエージェントへの委譲を許可
  
  # カスタムツール（tools/ ディレクトリで定義）
  my_custom_tool: true
```

### テンプレート変数

プロンプト内で使用できる変数：

| 変数 | 説明 |
|------|------|
| `{{CURRENT_DATETIME}}` | 現在の日時 |
| `{{WORKING_DIRECTORY}}` | 作業ディレクトリのパス |
| `{{PROFILE_NAME}}` | プロファイル名 |

### orchestrator.md（メインエージェント）

```markdown
---
description: メインオーケストレーター
mode: primary
tools:
  read_file: true
  write_file: true
  edit_file: true
  execute_bash: true
  delegate_to_agent: true
---

あなたはプロジェクトのメインオーケストレーターです。

## 委譲可能なエージェント

- **coder**: コード実装を担当
- **reviewer**: コードレビューを担当

## ワークフロー

1. タスクを分析
2. 適切なエージェントに委譲
3. 結果を統合して報告

現在時刻: {{CURRENT_DATETIME}}
```

### サブエージェント（coder.md）

```markdown
---
description: コード実装エージェント
mode: primary
tools:
  read_file: true
  write_file: true
  edit_file: true
  execute_bash: true
  grep: true
---

あなたはコード実装を担当するエンジニアです。

## 責任

- 機能の実装
- テストの作成
- バグ修正

## コーディング規約

- PEP 8 に準拠
- 型ヒントを使用
- docstring を記述
```

---

## 委譲（delegate_to_agent）

orchestrator から他のエージェントにタスクを委譲できます。

### 使用例（orchestrator.md 内）

```markdown
## 委譲ルール

複雑なタスクは専門エージェントに委譲してください：

- コード実装 → `coder` に委譲
- コードレビュー → `reviewer` に委譲
- セキュリティ監査 → `security-analyst` に委譲

委譲時は具体的なタスク内容を伝えてください：

```
delegate_to_agent(
    agent_name="coder",
    task="src/utils.py に add_numbers(a, b) 関数を実装してください"
)
```
```

---

## ベストプラクティス

### 1. 明確な役割分担

```
orchestrator: タスク分析、委譲、結果統合
coder: 実装
reviewer: レビュー
tester: テスト
```

### 2. ツールは最小限に

```yaml
# ❌ 全ツールを有効化
tools:
  read_file: true
  write_file: true
  execute_bash: true
  websearch: true
  # ... 30個のツール

# ✅ 必要なツールのみ
tools:
  read_file: true
  write_file: true
  grep: true
```

### 3. 具体的な指示

```markdown
# ❌ 曖昧
あなたはエンジニアです。コードを書いてください。

# ✅ 具体的
あなたは Python バックエンドエンジニアです。

## コーディング規約
- PEP 8 準拠
- 型ヒント必須
- docstring は Google スタイル

## 禁止事項
- グローバル変数の使用
- 外部 API への直接アクセス（utils/api.py を使用）
```

### 4. エラーハンドリング指示

```markdown
## エラー時の対応

1. エラーメッセージを読む
2. 原因を特定
3. 修正を試みる
4. 3回失敗したら報告

```bash
# 例: テスト失敗時
pytest tests/ -v
# → 失敗したテストを確認
# → コードを修正
# → 再テスト
```
```

---

## 使用例

### プロファイルの使用

```bash
# CLI から指定
moco run "タスク" --profile my-profile

# Python API から
from moco import Orchestrator

o = Orchestrator(profile="my-profile")
result = o.run_sync("タスク")
```

### プロファイル一覧

```bash
moco list-profiles
```
