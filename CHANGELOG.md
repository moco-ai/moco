# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- (予定) ストリーミングレスポンス対応
- (予定) Claude プロバイダ対応

### Changed
- (予定) なし

### Fixed
- (予定) なし

---

## [0.1.0] - 2026-01-08

🎉 **初回リリース** - moco-agent の最初の公開バージョン

### Added

#### コアフレームワーク
- **マルチプロバイダ対応**: Gemini、OpenAI、OpenRouter の 3 つの LLM プロバイダをサポート
  - `LLMProvider` クラスによる統一的な API インターフェース
  - プロバイダ間の自動フォールバック機能
  - プロバイダ固有の設定（温度、最大トークン数など）のカスタマイズ

- **プロファイル機能**: ドメイン特化型エージェントの定義システム
  - YAML ベースのプロファイル設定
  - プロファイル固有のシステムプロンプト
  - プロファイル固有のツールセット
  - 動的プロファイル検出とロード

- **Orchestrator**: 会話とツール実行を管理する中央コントローラー
  - マルチターン会話のサポート
  - ツール呼び出しの自動処理
  - 会話履歴の管理

#### メモリ・コンテキスト管理
- **自動コンテキスト圧縮** (`ContextCompressor`)
  - トークン制限に基づく自動圧縮
  - 重要度ベースのメッセージ選択
  - 圧縮履歴の要約生成

- **セマンティックメモリ** (`SemanticMemory`)
  - FAISS ベースのベクトル検索
  - Gemini Embeddings による埋め込み生成
  - 類似度ベースの記憶検索

- **チェックポイント/復元** (`CheckpointManager`)
  - 会話状態の保存と復元
  - 複数チェックポイントの管理
  - JSON ベースの永続化

#### セキュリティ・品質管理
- **ガードレール** (`Guardrails`)
  - 入力バリデーション（禁止パターン、長さ制限）
  - 出力バリデーション（機密情報フィルタリング）
  - ツール実行制御（許可リスト、拒否リスト）
  - カスタムバリデータのサポート

#### 外部連携
- **MCP (Model Context Protocol) 対応** (`MCPClient`)
  - MCP サーバーとの接続
  - 外部ツールの動的ロード
  - stdio/SSE トランスポートのサポート

- **OpenTelemetry 統合** (`TelemetryManager`)
  - 分散トレーシング
  - メトリクス収集
  - ログ相関

#### ツール
- **ファイルシステムツール**
  - `read_file`: ファイル読み込み（行範囲指定可）
  - `write_file`: ファイル書き込み
  - `edit_file`: 部分編集（検索・置換）
  - `list_dir`: ディレクトリ一覧

- **検索ツール**
  - `grep`: 正規表現検索
  - `glob_search`: パターンマッチング検索
  - `codebase_search`: セマンティックコード検索

- **Web ツール**
  - `webfetch`: URL コンテンツ取得
  - `websearch`: Web 検索

- **プロセスツール**
  - `bash`: シェルコマンド実行

- **タスク管理ツール**
  - `todoread`: TODO リスト読み込み
  - `todowrite`: TODO リスト書き込み

#### CLI
- `moco run`: エージェント実行
- `moco chat`: インタラクティブチャット
- `moco profiles`: プロファイル一覧表示
- `moco tools`: ツール一覧表示

#### その他
- MIT ライセンスでの公開
- Python 3.10/3.11/3.12 サポート
- 型ヒント完備（mypy 対応）
- ruff によるコードフォーマット/リント設定

### Dependencies
- `google-genai>=1.0.0`
- `openai>=1.12.0`
- `faiss-cpu>=1.7.4`
- `numpy>=1.24.0`
- `pyyaml>=6.0`
- `typer>=0.9.0`
- `rich>=13.0.0`
- `python-dotenv>=1.0.0`
- `httpx>=0.26.0`

---

## バージョン番号の付け方

このプロジェクトは [Semantic Versioning](https://semver.org/spec/v2.0.0.html) に従います。

- **MAJOR** (1.0.0): 後方互換性のない変更
- **MINOR** (0.1.0): 後方互換性のある機能追加
- **PATCH** (0.0.1): 後方互換性のあるバグ修正

### プレリリースバージョン

- `0.x.x`: 初期開発フェーズ。API は安定していません
- `1.0.0`: 最初の安定版リリース（予定）

---

## リンク

[Unreleased]: https://github.com/moco-team/moco-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/moco-team/moco-agent/releases/tag/v0.1.0
