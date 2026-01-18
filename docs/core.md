# Core API リファレンス

moco のコアモジュールの API リファレンスです。

---

## Orchestrator

メインのオーケストレータークラス。ユーザー入力を適切なエージェントにルーティングし、会話履歴を管理します。

### クラス定義

::: moco.core.orchestrator.Orchestrator
    options:
      show_root_heading: true
      show_source: false
      members:
        - __init__
        - process_message
        - create_session
        - get_session_history
        - continue_session
        - save_checkpoint
        - restore_from_checkpoint
        - list_checkpoints
        - run
        - run_sync
        - reload_agents

### 基本的な使用例

```python
from moco.core import Orchestrator

# 初期化
orchestrator = Orchestrator(
    profile="development",    # プロファイル名
    provider="gemini",        # LLM プロバイダ
    model="gemini-2.0-flash", # モデル名
    stream=True,              # ストリーミング出力
    verbose=False,            # 詳細ログ
    auto_compress=True        # 自動コンテキスト圧縮
)

# セッション作成
session_id = orchestrator.create_session(title="開発タスク")

# メッセージ処理
response = orchestrator.process_message(
    "READMEを更新してください",
    session_id=session_id
)
```

### 非同期実行

```python
import asyncio
from moco.core import Orchestrator

async def main():
    orchestrator = Orchestrator()
    session_id = orchestrator.create_session()
    
    response = await orchestrator.run(
        "ファイル一覧を表示してください",
        session_id=session_id
    )
    print(response)

asyncio.run(main())
```

### チェックポイント管理

```python
from moco.core import Orchestrator

orchestrator = Orchestrator()
session_id = orchestrator.create_session()

# いくつかのメッセージを処理
orchestrator.process_message("タスク1", session_id)
orchestrator.process_message("タスク2", session_id)

# 手動でチェックポイントを保存
checkpoint = orchestrator.save_checkpoint(
    session_id,
    context_summary="タスク1とタスク2を完了"
)

# チェックポイント一覧
checkpoints = orchestrator.list_checkpoints(session_id)

# チェックポイントから復元
restored = orchestrator.restore_from_checkpoint(
    checkpoint_id=checkpoint.checkpoint_id
)
```

---

## AgentRuntime

個々のエージェントの実行環境を管理するクラス。

### クラス定義

::: moco.core.runtime.AgentRuntime
    options:
      show_root_heading: true
      show_source: false
      members:
        - __init__
        - run

### 使用例

```python
from moco.core import AgentRuntime
from moco.tools.discovery import AgentConfig

# エージェント設定
config = AgentConfig(
    name="my-agent",
    description="カスタムエージェント",
    system_prompt="あなたは親切なアシスタントです。",
    tools=["read_file", "write_file"]
)

# ツールマップ
from moco.tools import TOOL_MAP

# ランタイム作成
runtime = AgentRuntime(
    config=config,
    tool_map=TOOL_MAP,
    provider="gemini",
    stream=True
)

# 実行
response = runtime.run("Hello!")
```

---

## LLMProvider

サポートされている LLM プロバイダの定数。

```python
from moco.core.runtime import LLMProvider

LLMProvider.GEMINI      # "gemini"
LLMProvider.OPENAI      # "openai"
LLMProvider.OPENROUTER  # "openrouter"
```

### プロバイダ別の環境変数

| プロバイダ | API キー | モデル指定 |
|-----------|---------|-----------|
| `gemini` | `GEMINI_API_KEY` | `GEMINI_MODEL` |
| `openai` | `OPENAI_API_KEY` | `OPENAI_MODEL` |
| `openrouter` | `OPENROUTER_API_KEY` | `OPENROUTER_MODEL` |

---

## ContextCompressor

会話履歴の自動圧縮を行うクラス。トークン数がしきい値を超えた場合、古いメッセージを要約してコンテキストウィンドウ内に収めます。

### クラス定義

::: moco.core.context_compressor.ContextCompressor
    options:
      show_root_heading: true
      show_source: false
      members:
        - __init__
        - estimate_tokens
        - compress_if_needed

### 使用例

```python
from moco.core import ContextCompressor

compressor = ContextCompressor(
    max_tokens=100000,      # 圧縮開始しきい値
    preserve_recent=10,     # 保持する直近メッセージ数
    summary_model="gemini-2.0-flash",
    compression_ratio=0.5
)

# メッセージリスト（OpenAI形式またはGemini形式）
messages = [
    {"role": "user", "content": "質問1"},
    {"role": "assistant", "content": "回答1"},
    # ... 多数のメッセージ
]

# 必要に応じて圧縮
compressed, was_compressed = compressor.compress_if_needed(
    messages,
    provider="gemini"
)

if was_compressed:
    print("コンテキストが圧縮されました")
```

### 圧縮戦略

1. システムメッセージは常に保持
2. 直近 `preserve_recent` 件のメッセージは保持
3. それより古いメッセージは LLM で要約して1つの assistant メッセージに圧縮

---

## Guardrails

入力・出力・ツール呼び出しに対する検証とフィルタリングを提供するクラス。

### クラス定義

::: moco.core.guardrails.Guardrails
    options:
      show_root_heading: true
      show_source: false
      members:
        - __init__
        - validate_input
        - validate_output
        - validate_tool_call
        - add_blocked_pattern
        - add_blocked_tool
        - remove_blocked_tool
        - set_allowed_tools
        - add_input_validator
        - add_output_validator
        - add_tool_validator

### 使用例

```python
from moco.core import Guardrails, GuardrailAction, GuardrailResult

# 基本設定
guardrails = Guardrails(
    max_input_length=100000,
    max_output_length=50000,
    blocked_patterns=[r"password", r"secret"],
    blocked_tools=["execute_bash"],
    max_tool_calls_per_turn=20,
    enable_dangerous_pattern_check=True
)

# 入力検証
result = guardrails.validate_input("ユーザー入力")
if result.is_blocked():
    print(f"ブロック: {result.message}")

# カスタムバリデーターの追加
def my_validator(text: str) -> GuardrailResult:
    if "危険" in text:
        return GuardrailResult(
            action=GuardrailAction.WARN,
            message="危険なキーワードが含まれています"
        )
    return GuardrailResult(action=GuardrailAction.ALLOW)

guardrails.add_input_validator(my_validator)
```

詳細は [ガードレール設定ガイド](../guides/guardrails.md) を参照してください。

---

## GuardrailAction

ガードレールの検証結果アクション。

```python
from moco.core import GuardrailAction

GuardrailAction.ALLOW   # 許可
GuardrailAction.BLOCK   # ブロック
GuardrailAction.MODIFY  # 修正して続行
GuardrailAction.WARN    # 警告を出して続行
```

---

## GuardrailResult

ガードレール検証の結果を表すデータクラス。

```python
from moco.core import GuardrailResult, GuardrailAction

result = GuardrailResult(
    action=GuardrailAction.BLOCK,
    message="ブロックされました",
    modified_content=None  # MODIFY の場合に使用
)

result.is_allowed()  # True if ALLOW, WARN, or MODIFY
result.is_blocked()  # True if BLOCK
```

---

## Telemetry

OpenTelemetry 統合クラス。トレースとメトリクスの収集・エクスポートを提供します。

### クラス定義

::: moco.core.telemetry.Telemetry
    options:
      show_root_heading: true
      show_source: false
      members:
        - __init__
        - span
        - record_llm_call
        - record_tool_call

### 使用例

```python
from moco.core import Telemetry, TelemetryConfig

# 設定
config = TelemetryConfig(
    enabled=True,
    service_name="my-app",
    otlp_endpoint="http://localhost:4317",
    console_export=True  # デバッグ用
)

telemetry = Telemetry(config)

# スパンの作成
with telemetry.span("my_operation", {"key": "value"}):
    # 処理
    pass

# メトリクス記録
telemetry.record_llm_call(
    provider="gemini",
    model="gemini-2.0-flash",
    input_tokens=100,
    output_tokens=200,
    latency_ms=500.0,
    success=True
)
```

### 環境変数

| 変数 | 説明 |
|------|------|
| `OTEL_ENABLED` | テレメトリ有効化（`true`/`1`/`yes`） |
| `OTEL_SERVICE_NAME` | サービス名 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP エンドポイント |
| `OTEL_CONSOLE_EXPORT` | コンソール出力有効化 |

---

## CheckpointManager

セッション状態の永続化・復元を行うクラス。

### クラス定義

::: moco.core.checkpoint.CheckpointManager
    options:
      show_root_heading: true
      show_source: false
      members:
        - __init__
        - save
        - load
        - load_latest
        - list_checkpoints
        - delete
        - should_auto_save

### 使用例

```python
from moco.core import CheckpointManager, CheckpointConfig

config = CheckpointConfig(
    enabled=True,
    storage_dir=".moco/checkpoints",
    auto_save_interval=5,           # 5ターンごとに自動保存
    max_checkpoints_per_session=10  # 最大保存数
)

manager = CheckpointManager(config)

# チェックポイント保存
checkpoint = manager.save(
    session_id="session-123",
    conversation_history=[...],
    context_summary="作業の要約",
    metadata={"turn_count": 10}
)

# 最新のチェックポイントを読み込み
latest = manager.load_latest("session-123")

# 一覧取得
checkpoints = manager.list_checkpoints("session-123")
```

---

## MCPClient

MCP (Model Context Protocol) サーバーと通信するクライアント。

### クラス定義

::: moco.core.mcp_client.MCPClient
    options:
      show_root_heading: true
      show_source: false
      members:
        - __init__
        - connect
        - disconnect
        - get_tool_definitions
        - create_tool_functions
        - call_tool

### 使用例

```python
from moco.core import MCPClient, MCPConfig, MCPServerConfig

config = MCPConfig(
    enabled=True,
    servers=[
        MCPServerConfig(
            name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        )
    ]
)

client = MCPClient(config)
await client.connect()

# ツール定義を取得
tools = client.get_tool_definitions()

# ツール関数を作成
tool_functions = client.create_tool_functions()

# ツールを呼び出し
result = await client.call_tool("filesystem__read_file", {"path": "/tmp/test.txt"})

await client.disconnect()
```

---

## AgentConfig

エージェント設定を表すデータクラス。

```python
from moco.tools.discovery import AgentConfig

config = AgentConfig(
    name="my-agent",
    description="エージェントの説明",
    system_prompt="システムプロンプト",
    tools=["read_file", "write_file"],
    mode="chat"  # "chat" or "primary"
)
```

---

## AgentLoader

プロファイルからエージェント定義を読み込むクラス。

```python
from moco.tools.discovery import AgentLoader

loader = AgentLoader(profile="development")
agents = loader.load_agents()

for name, config in agents.items():
    print(f"{name}: {config.description}")
```
