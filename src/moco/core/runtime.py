import asyncio
import os
import json
import inspect
import hashlib
from ..cancellation import check_cancelled
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Callable, Union, get_type_hints
import sys
from collections import defaultdict

from ..tools.skill_loader import SkillConfig


class ToolCallTracker:
    """同じツール呼び出しのループを検出・防止するトラッカー"""
    
    def __init__(self, max_repeats: int = 3, window_size: int = 10):
        """
        Args:
            max_repeats: 同じ呼び出しが許可される最大回数
            window_size: チェック対象の直近の呼び出し数
        """
        self.history: List[str] = []
        self.max_repeats = max_repeats
        self.window_size = window_size
        self.blocked_calls: Dict[str, int] = defaultdict(int)
    
    def _make_key(self, tool_name: str, args: dict) -> str:
        """ツール呼び出しのユニークキーを生成（引数が大きい場合はハッシュ化）"""
        try:
            args_str = json.dumps(args, sort_keys=True, default=str)
            # 引数が100文字を超える場合はMD5ハッシュを使用
            if len(args_str) > 100:
                args_hash = hashlib.md5(args_str.encode()).hexdigest()
                return f"{tool_name}:hash:{args_hash}"
            return f"{tool_name}:{args_str}"
        except Exception:
            return f"{tool_name}:{str(args)}"
    
    def check_and_record(self, tool_name: str, args: dict) -> tuple[bool, str]:
        """
        ツール呼び出しをチェックし、履歴に記録する。
        
        Returns:
            (allowed: bool, message: str)
            - allowed=True: 実行許可
            - allowed=False: ループ検出、実行ブロック
        """
        call_key = self._make_key(tool_name, args)
        
        # 直近の呼び出しで同じキーをカウント
        recent = self.history[-self.window_size:] if len(self.history) >= self.window_size else self.history
        repeat_count = sum(1 for h in recent if h == call_key)
        
        if repeat_count >= self.max_repeats:
            self.blocked_calls[call_key] += 1
            return False, (
                f"⚠️ ループ検出: {tool_name} が同じ引数で {repeat_count} 回呼び出されました。\n"
                f"このツール呼び出しはブロックされました。\n"
                f"別のアプローチを試すか、異なる引数を使用してください。"
            )
        
        self.history.append(call_key)
        return True, ""
    
    def reset(self):
        """履歴をリセット"""
        self.history.clear()
        self.blocked_calls.clear()

# Gemini
from google import genai
from google.genai import types

# OpenAI
try:
    from openai import OpenAI, AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

from ..tools.discovery import AgentConfig
from ..storage.semantic_memory import SemanticMemory
from .context_compressor import ContextCompressor

# ツール使用ログ用
MAX_ARG_LEN = 40  # 引数の最大文字数

class StreamPrintState:
    """stdout状態を管理するクラス（テスト時にリセット可能）"""
    broken = False
    
    @classmethod
    def reset(cls):
        """テスト用：状態をリセット"""
        cls.broken = False


def _safe_stream_print(text: str) -> None:
    """ストリーミング表示用の安全な print。

    Web UI 実行時などで stdout が閉じられていると BrokenPipeError / OSError(errno=32) が出るため、
    以後の標準出力を抑制して処理自体は継続する。
    """
    if StreamPrintState.broken:
        return
    try:
        print(text, end="", flush=True)
    except BrokenPipeError:
        StreamPrintState.broken = True
    except OSError as e:
        if getattr(e, "errno", None) == 32:
            StreamPrintState.broken = True
            return
        raise

# ツール種類別のアイコンと色
TOOL_STYLES = {
    # ファイル操作
    "read_file": ("📖", "cyan"),
    "write_file": ("📝", "green"),
    "edit_file": ("✏️", "yellow"),
    # 実行系
    "execute_bash": ("⚡", "magenta"),
    # 委譲
    "delegate_to_agent": ("👤", "blue"),
    # 検索系
    "websearch": ("🔍", "cyan"),
    "webfetch": ("🌐", "cyan"),
    "grep": ("🔎", "dim"),
    "codebase_search": ("🔍", "cyan"),
    # ディレクトリ
    "list_dir": ("📁", "dim"),
    "glob_search": ("📂", "dim"),
    # 計算系
    "calculate_tax": ("🧮", "green"),
    # デフォルト
    "_default": ("🔧", "dim"),
}

def _format_tool_log(tool_name: str, args: dict) -> tuple:
    """ツールログをフォーマット。(icon, name, arg_str, color) を返す"""
    style = TOOL_STYLES.get(tool_name, TOOL_STYLES["_default"])
    icon, color = style

    # 引数を抽出
    arg_str = ""

    if tool_name in ("read_file", "write_file", "edit_file"):
        path = args.get("path") or args.get("file_path") or ""
        # ファイル名だけ抽出
        if path and "/" in path:
            arg_str = path.split("/")[-1]
        else:
            arg_str = path or ""
        # offset/limit も表示（使用している場合）
        offset = args.get("offset")
        limit = args.get("limit")
        if offset or limit:
            start = offset or 1
            end = start + (limit or 0) - 1 if limit else "end"
            arg_str += f" [L{start}-{end}]"

    elif tool_name == "execute_bash":
        cmd = args.get("command") or ""
        # 最初のコマンドだけ
        arg_str = cmd.split()[0] if cmd and cmd.split() else (cmd or "")
        if cmd and len(cmd) > len(arg_str) + 5:
            arg_str += " ..."

    elif tool_name == "delegate_to_agent":
        arg_str = f"@{args.get('agent_name') or ''}"

    elif tool_name in ("websearch", "codebase_search"):
        q = args.get("query") or ""
        arg_str = q[:30] + "..." if len(q) > 30 else q

    elif tool_name == "grep":
        pattern = args.get("pattern") or ""
        arg_str = pattern[:20]

    elif tool_name in ("list_dir", "glob_search"):
        path = args.get("target_dir") or args.get("path") or ""
        if path and "/" in path:
            arg_str = path.split("/")[-1] or path.split("/")[-2] or ""
        else:
            arg_str = path or ""

    elif tool_name == "webfetch":
        url = args.get("url") or ""
        # ドメインだけ
        if url and "://" in url:
            arg_str = url.split("://")[1].split("/")[0]
        else:
            arg_str = url[:25] if url else ""

    else:
        # その他: 最初の引数
        for k, v in list(args.items())[:1]:
            v_str = str(v)
            if len(v_str) > 25:
                v_str = v_str[:25] + "..."
            arg_str = v_str
            break

    # 長さ制限
    if len(arg_str) > MAX_ARG_LEN:
        arg_str = arg_str[:MAX_ARG_LEN - 3] + "..."

    return icon, tool_name, arg_str, color

try:
    from rich.console import Console
    _tool_console = Console()

    def _log_tool_use(tool_name: str, args: dict = None, verbose: bool = False):
        """ツール使用を簡潔にログ"""
        if verbose:
            pass  # verbose の場合は詳細ログが別途出力される
        else:
            icon, name, arg_str, color = _format_tool_log(tool_name, args or {})
            # ツール名を固定幅で揃える
            name_padded = name[:18].ljust(18)
            if arg_str:
                _tool_console.print(f"    {icon} [{color}]{name_padded}[/{color}] [dim]→ {arg_str}[/dim]")
            else:
                _tool_console.print(f"    {icon} [{color}]{name_padded}[/{color}]")
except ImportError:
    def _log_tool_use(tool_name: str, args: dict = None, verbose: bool = False):
        if not verbose:
            icon, name, arg_str, color = _format_tool_log(tool_name, args or {})
            name_padded = name[:18].ljust(18)
            if arg_str:
                print(f"    {icon} {name_padded} → {arg_str}")
            else:
                print(f"    {icon} {name_padded}")


def _validate_arguments(func: Callable, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    関数の引数の型をバリデーションし、可能な限り変換する。
    """
    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}

    validated_args = {}
    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        
        if param_name in args:
            val = args[param_name]
            expected_type = hints.get(param_name)
            
            if expected_type:
                # 簡易的な型変換/チェック
                if expected_type is int and not isinstance(val, int):
                    try:
                        val = int(val)
                    except (ValueError, TypeError):
                        pass
                elif expected_type is float and not isinstance(val, (int, float)):
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        pass
                elif expected_type is bool and not isinstance(val, bool):
                    if str(val).lower() in ("true", "1", "yes"):
                        val = True
                    elif str(val).lower() in ("false", "0", "no"):
                        val = False
            
            validated_args[param_name] = val
        elif param.default != inspect.Parameter.empty:
            # デフォルト値がある場合は何もしない（func(**validated_args)でデフォルトが使われる）
            pass
            
    return validated_args


def _execute_tool_safely(func: Callable, args: Dict[str, Any]) -> Any:
    """
    ツールを安全に実行する。async関数の場合は同期的に実行する。
    """
    valid_args = _validate_arguments(func, args)
    result = func(**valid_args)
    
    # async 関数の場合は同期的に実行
    if asyncio.iscoroutine(result):
        try:
            # Python 3.10+: get_running_loop()を使用して実行中のループを確認
            loop = asyncio.get_running_loop()
            # 実行中のループ内からは新しいスレッドで実行
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, result)
                result = future.result(timeout=60)
        except RuntimeError:
            # ループが実行中でない場合は asyncio.run() を使用
            result = asyncio.run(result)
    
    return result


async def _execute_tool_safely_async(func: Callable, args: Dict[str, Any]) -> Any:
    """
    ツールを安全に実行する（async版）。
    - async tool / coroutine 戻り値は await で解決する
    - 同期ツールはイベントループをブロックしないよう to_thread で実行する
    """
    valid_args = _validate_arguments(func, args)

    # まずは通常呼び出し
    if asyncio.iscoroutinefunction(func):
        result = await func(**valid_args)
    else:
        result = await asyncio.to_thread(func, **valid_args)

    # 念のため coroutine 返り値も解決
    if asyncio.iscoroutine(result):
        result = await result
    return result


# ツール出力の最大文字数（これを超えると一時ファイルに保存）
MAX_TOOL_OUTPUT_CHARS = 50000
_TEMP_OUTPUT_DIR = "/tmp/moco_tool_outputs"

# コンテキスト上限管理（1回のエージェント実行内）
MAX_CONTEXT_TOKENS = 150000      # 入力コンテキストの上限（約150K tokens）
# MAX_TOOL_CALLS = 15            # コメントアウト: ContextCompressor で管理
CONTEXT_WARNING_THRESHOLD = 0.8  # 80%で警告・圧縮発動


def _gemini_messages_to_dict(messages: List[Any]) -> List[Dict[str, Any]]:
    """Gemini の types.Content を Dict 形式に変換"""
    result = []
    for msg in messages:
        if hasattr(msg, 'role') and hasattr(msg, 'parts'):
            # types.Content オブジェクト
            parts_text = []
            for part in msg.parts:
                if hasattr(part, 'text') and part.text:
                    parts_text.append(part.text)
                elif hasattr(part, 'function_call'):
                    parts_text.append(f"[function_call: {part.function_call.name}]")
                elif hasattr(part, 'function_response'):
                    parts_text.append(f"[function_response: {part.function_response.name}]")
            result.append({
                "role": msg.role,
                "content": "\n".join(parts_text)
            })
        elif isinstance(msg, dict):
            result.append(msg)
    return result


def _dict_to_gemini_messages(messages: List[Dict[str, Any]]) -> List[Any]:
    """Dict 形式を Gemini の types.Content に変換"""
    result = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        # model -> assistant の変換（Gemini は model を使う）
        if role == "assistant":
            role = "model"
        result.append(types.Content(role=role, parts=[types.Part(text=content)]))
    return result


# 全エージェント共通ルール（システムプロンプトに自動注入）
COMMON_AGENT_RULES = """
## ⛔ ツール呼び出し上限時のルール

### 自分が上限に達した場合
「⛔ ツール呼び出し上限到達」メッセージが表示されたら:
1. 現在までの作業結果をまとめる
2. 以下のJSON形式で残りタスクを返す:

```json
{
  "status": "interrupted",
  "completed": ["完了したタスク1", "完了したタスク2"],
  "remaining": ["残りタスク1", "残りタスク2"],
  "context": "継続に必要な情報（対象ファイル、現在の状態等）"
}
```

### 委譲先から中断を受け取った場合
委譲先エージェントから `"status": "interrupted"` を含む応答を受け取ったら:
1. `remaining` の内容を確認
2. 同じエージェントに `remaining` タスクと `context` を渡して再度委譲
3. 完了まで繰り返す

## 📄 出力が切り詰められた場合のルール

ツール出力に以下のメッセージが表示された場合:
- `⚠️ OUTPUT TRUNCATED` または `Content truncated`
- `👉 NEXT STEP:` で始まる指示

**必ず指示されたコマンドを実行して続きを読んでください。**
- 同じリクエストを繰り返さないこと
- 表示されたパス・offset・limit をそのまま使用すること
- 続きを読み終わるまで繰り返すこと
"""

def _estimate_tokens(text: str) -> int:
    """トークン数を推定（簡易: 4文字 ≒ 1トークン）"""
    return len(text) // 4

def _truncate_tool_output(result: Any, tool_name: str) -> str:
    """
    ツール出力が長すぎる場合、一時ファイルに保存して参照を返す。
    """
    if result is None:
        return "No output"
    
    result_str = str(result)
    
    if len(result_str) <= MAX_TOOL_OUTPUT_CHARS:
        return result_str
    
    # 長すぎる場合は一時ファイルに保存
    import os
    import time
    
    os.makedirs(_TEMP_OUTPUT_DIR, exist_ok=True)
    filename = f"{tool_name}_{int(time.time() * 1000)}.txt"
    filepath = os.path.join(_TEMP_OUTPUT_DIR, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(result_str)
    
    # 先頭部分を表示し、残りはファイル参照
    preview = result_str[:500]
    total_lines = result_str.count('\n') + 1
    total_chars = len(result_str)
    
    return (
        f"{preview}\n\n"
        f"⚠️ OUTPUT TRUNCATED ⚠️\n"
        f"Total: {total_chars:,} chars, {total_lines:,} lines\n"
        f"Full output saved to: {filepath}\n\n"
        f"👉 NEXT STEP: 以下を実行して続きを読んでください:\n"
        f"   read_file(path=\"{filepath}\", offset=1, limit=10000)\n"
    )


def _ensure_jsonable(value: Any) -> Any:
    """JSON化できない値（coroutine等）は文字列化して返す。"""
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


# LLMプロバイダー設定
class LLMProvider:
    GEMINI = "gemini"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    ZAI = "zai"  # Z.ai GLM-4.7


def _is_reasoning_model(model_name: str) -> bool:
    """reasoning/thinking 対応モデルかどうかを判定
    
    - OpenAI: o1, o3, o4 系
    - Gemini (OpenRouter経由): gemini-2.5, gemini-3 系
    """
    model_lower = model_name.lower()
    # OpenAI reasoning モデル
    openai_patterns = ["o1", "o3", "o4"]
    # Gemini thinking 対応モデル (OpenRouter経由: google/gemini-...)
    gemini_patterns = ["gemini-2.5", "gemini-3", "gemini-2.0-flash-thinking"]
    
    all_patterns = openai_patterns + gemini_patterns
    return any(pattern in model_lower for pattern in all_patterns)


def _python_type_to_schema(py_type) -> Dict[str, Any]:
    """Python型をJSONスキーマに変換"""
    type_map = {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
        list: {"type": "array"},
        dict: {"type": "object"},
    }
    return type_map.get(py_type, {"type": "string"})


def _func_to_openai_tool(func: Callable, tool_name: str) -> Dict[str, Any]:
    """Python関数をOpenAIのtool形式に変換"""
    sig = inspect.signature(func)
    docstring = func.__doc__ or ""
    # docstring 全体を description として使用（フォーマット例などを LLM に伝える）
    description = docstring.strip() if docstring else f"{tool_name} function"

    properties = {}
    required = []

    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue

        param_type = hints.get(param_name, str)
        schema = _python_type_to_schema(param_type)

        param_desc = f"Parameter: {param_name}"
        if param_name in docstring:
            lines = docstring.split("\n")
            for line in lines:
                if param_name in line and ":" in line:
                    param_desc = line.split(":", 1)[-1].strip()
                    break

        properties[param_name] = {**schema, "description": param_desc}

        if param.default == inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            }
        }
    }


def _func_to_declaration(func: Callable, tool_name: str) -> types.FunctionDeclaration:
    """Python関数をFunctionDeclarationに変換"""
    sig = inspect.signature(func)
    docstring = func.__doc__ or ""

    # docstring 全体を description として使用（フォーマット例などを LLM に伝える）
    description = docstring.strip() if docstring else f"{tool_name} function"

    # パラメータスキーマを構築
    properties = {}
    required = []

    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue

        # 型アノテーションがあればそれを使用
        param_type = hints.get(param_name, str)
        schema = _python_type_to_schema(param_type)

        # docstringからパラメータ説明を抽出（Args: セクション）
        param_desc = f"Parameter: {param_name}"
        if f"{param_name}" in docstring:
            # 簡易的な抽出
            lines = docstring.split("\n")
            for line in lines:
                if param_name in line and ":" in line:
                    param_desc = line.split(":", 1)[-1].strip()
                    break

        properties[param_name] = {**schema, "description": param_desc}

        # デフォルト値がなければ必須
        if param.default == inspect.Parameter.empty:
            required.append(param_name)

    # parameters引数にはSchema型が必要だが、dict構造を渡すと内部で変換されることが多い
    # エラーが出る場合は types.Schema(**...) でラップする必要があるかもしれない
    # 現時点では互換性のため辞書として渡す
    return types.FunctionDeclaration(
        name=tool_name,
        description=description,
        parameters={
            "type": "object",
            "properties": properties,
            "required": required,
        }
    )


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        tool_map: Dict[str, Callable],
        agent_name: str = "agent",
        name: str = None,
        provider: str = None,
        model: str = None,
        stream: bool = True,
        verbose: bool = False,
        progress_callback: Optional[Callable] = None,
        parent_agent: Optional[str] = None,
        semantic_memory: Optional[SemanticMemory] = None,
        skills: Optional[List[SkillConfig]] = None
    ):
        self.config = config
        self.tool_map = tool_map
        self.agent_name = agent_name
        self.name = name or agent_name
        self.verbose = verbose
        self.progress_callback = progress_callback
        self.parent_agent = parent_agent
        self.skills: List[SkillConfig] = skills or []
        
        # ツール呼び出しループ検出
        self.tool_tracker = ToolCallTracker(max_repeats=3, window_size=10)
        
        # セマンティックメモリの初期化
        self.semantic_memory = semantic_memory
        if not self.semantic_memory:
            from pathlib import Path
            db_path = os.getenv("SEMANTIC_DB_PATH", str(Path.cwd() / "data" / "semantic.db"))
            self.semantic_memory = SemanticMemory(db_path=db_path)

        # プロバイダーの決定（環境変数 or 引数 or 自動選択）
        from .llm_provider import get_available_provider
        if provider:
            self.provider = provider
        elif os.environ.get("LLM_PROVIDER"):
            self.provider = os.environ.get("LLM_PROVIDER")
        else:
            self.provider = get_available_provider()

        # モデル名の決定
        if model:
            self.model_name = model
        elif self.provider == LLMProvider.OPENROUTER:
            self.model_name = os.environ.get("OPENROUTER_MODEL", "google/gemini-3-flash-preview")  # reasoning対応
        elif self.provider == LLMProvider.OPENAI:
            self.model_name = os.environ.get("OPENAI_MODEL", "gpt-5.2-codex")
        elif self.provider == LLMProvider.ZAI:
            self.model_name = os.environ.get("ZAI_MODEL", "glm-4.7")
        else:
            self.model_name = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")

        # クライアントの初期化
        if self.provider == LLMProvider.OPENROUTER:
            # OpenRouter は OpenAI互換API
            if not OPENAI_AVAILABLE:
                raise ImportError("OpenAI package not installed. Run: pip install openai")
            openrouter_key = os.environ.get("OPENROUTER_API_KEY")
            if not openrouter_key:
                raise ValueError("OPENROUTER_API_KEY environment variable not set")
            self.openai_client = AsyncOpenAI(
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1"
            )
            self.client = None
        elif self.provider == LLMProvider.OPENAI:
            if not OPENAI_AVAILABLE:
                raise ImportError("OpenAI package not installed. Run: pip install openai")
            openai_key = os.environ.get("OPENAI_API_KEY")
            if not openai_key:
                raise ValueError("OPENAI_API_KEY environment variable not set")
            self.openai_client = AsyncOpenAI(api_key=openai_key)
            self.client = None
        elif self.provider == LLMProvider.ZAI:
            # Z.ai GLM-4.7 (OpenAI互換API)
            if not OPENAI_AVAILABLE:
                raise ImportError("OpenAI package not installed. Run: pip install openai")
            zai_key = os.environ.get("ZAI_API_KEY")
            if not zai_key:
                raise ValueError("ZAI_API_KEY environment variable not set")
            self.openai_client = AsyncOpenAI(
                api_key=zai_key,
                base_url="https://api.z.ai/api/coding/paas/v4"
            )
            self.client = None
        else:
            # Gemini
            api_key = (
                os.environ.get("GENAI_API_KEY") or
                os.environ.get("GEMINI_API_KEY") or
                os.environ.get("GOOGLE_API_KEY")
            )
            if not api_key:
                raise ValueError("GENAI_API_KEY, GEMINI_API_KEY, or GOOGLE_API_KEY not set")
            self.client = genai.Client(api_key=api_key)
            self.openai_client = None

        # ストリーミング設定
        self.stream = stream
        
        # 部分応答（エラー発生時に保存するため）
        self._partial_response = ""

        # コンテキスト上限管理（1回のrun()内でリセット）
        self._accumulated_tokens = 0
        self._tool_call_count = 0
        self._context_limit_reached = False

        # メトリクス記録用
        self.last_usage: Dict[str, Any] = {}

        # ツールの準備
        self.available_tools = {}
        self.tool_declarations = []  # Gemini用
        self.openai_tools = []       # OpenAI用

        self._prepare_tools()

    def _prepare_tools(self):
        """有効化されたツールを準備する"""
        if not self.config.tools:
            return

        for tool_name in self.config.tools:
            if tool_name in self.tool_map:
                func = self.tool_map[tool_name]
                self.available_tools[tool_name] = func

                # Gemini用 FunctionDeclaration
                declaration = _func_to_declaration(func, tool_name)
                self.tool_declarations.append(declaration)

                # OpenAI用 tool定義
                openai_tool = _func_to_openai_tool(func, tool_name)
                self.openai_tools.append(openai_tool)
            else:
                if self.verbose:
                    print(f"Warning: Tool '{tool_name}' not found in provided tool_map")

    def _update_context_usage(self, result: str) -> str:
        """
        ツール結果のトークンを累積し、上限チェックを行う。
        警告または上限メッセージを result に追加して返す。
        """
        result_tokens = _estimate_tokens(result)
        self._accumulated_tokens += result_tokens
        self._tool_call_count += 1
        
        usage_ratio = self._accumulated_tokens / MAX_CONTEXT_TOKENS
        
        # 100%超過: 強制終了指示
        if usage_ratio >= 1.0:
            self._context_limit_reached = True
            return (
                f"{result}\n\n"
                f"⛔ **コンテキスト上限到達** ({self._accumulated_tokens:,} / {MAX_CONTEXT_TOKENS:,} tokens)\n"
                f"これ以上ツールを呼び出さず、今ある情報で応答を完了してください。"
            )
        
        # ツール呼び出し回数上限 - コメントアウト: ContextCompressor で管理
        # if self._tool_call_count >= MAX_TOOL_CALLS:
        #     self._context_limit_reached = True
        #     return (
        #         f"{result}\n\n"
        #         f"⛔ **ツール呼び出し上限到達** ({self._tool_call_count} / {MAX_TOOL_CALLS} calls)\n"
        #         f"これ以上ツールを呼び出さず、今ある情報で応答を完了してください。"
        #     )
        
        # 80%警告
        if usage_ratio >= CONTEXT_WARNING_THRESHOLD:
            remaining = MAX_CONTEXT_TOKENS - self._accumulated_tokens
            return (
                f"{result}\n\n"
                f"⚠️ コンテキスト {usage_ratio*100:.0f}% 使用中 "
                f"(残り約 {remaining:,} tokens)\n"
                f"必要最小限のツール呼び出しに抑えてください。"
            )
        
        return result

    def _get_system_prompt(self) -> str:
        """システムプロンプトを取得し、文脈情報を挿入する"""
        # JST (UTC+9)
        jst = timezone(timedelta(hours=9))
        now_dt = datetime.now(jst)
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S (JST)")

        # コンテキスト情報の構築
        context_header = f"---\n[Current Context]\nTimestamp: {now_str}\n"
        
        # 想起結果の追加
        if hasattr(self, "_recall_results") and self._recall_results:
            context_header += "\n[Related Knowledge/Past Incidents]\n"
            for i, res in enumerate(self._recall_results):
                content = res.get('content', '')
                # 長すぎる場合は切り詰め
                if len(content) > 1000:
                    content = content[:1000] + "..."
                context_header += f"- Knowledge {i+1}:\n{content}\n"
        
        context_header += "---\n\n"

        prompt = self.config.system_prompt

        # Skills の注入
        if self.skills:
            skills_section = "\n\n## Skills\n\n"
            for i, skill in enumerate(self.skills, 1):
                skills_section += f"### Skill: {skill.name} (v{skill.version})\n"
                skills_section += f"{skill.description}\n"
                if skill.allowed_tools:
                    skills_section += f"\n**Allowed Tools**: {', '.join(skill.allowed_tools)}\n"
                skills_section += f"\n{skill.content}\n\n"
            prompt += skills_section

        # プレースホルダー置換
        # ヘッダーと重複する場合があるが、プロンプト内の特定位置に日時を埋め込みたい要望に対応
        if "{{CURRENT_DATETIME}}" in prompt:
            prompt = prompt.replace("{{CURRENT_DATETIME}}", now_str)
        
        # セッションコンテキスト（mdで {{SESSION_CONTEXT}} を使った場合のみ注入）
        if "{{SESSION_CONTEXT}}" in prompt:
            session_context = self._build_session_context()
            prompt = prompt.replace("{{SESSION_CONTEXT}}", session_context)
        
        # エージェント統計（mdで {{AGENT_STATS}} を使った場合のみ注入）
        if "{{AGENT_STATS}}" in prompt:
            agent_stats = self._build_agent_stats()
            prompt = prompt.replace("{{AGENT_STATS}}", agent_stats)

        # 共通ルールを末尾に追加
        return context_header + prompt + "\n\n" + COMMON_AGENT_RULES
    
    def _build_session_context(self) -> str:
        """現在のセッションコンテキストを構築"""
        try:
            from ..tools.stats import get_session_stats
            return get_session_stats()
        except Exception as e:
            return f"(セッションコンテキスト取得エラー: {e})"
    
    def _build_agent_stats(self) -> str:
        """エージェント統計を構築"""
        try:
            from ..tools.stats import get_agent_stats
            return get_agent_stats(days=7)
        except Exception as e:
            return f"(エージェント統計取得エラー: {e})"

    async def _execute_tool_with_tracking(
        self, 
        func_name: str, 
        args_dict: Dict[str, Any], 
        session_id: Optional[str] = None
    ) -> str:
        """ツール実行とトラッキングの共通処理
        
        Args:
            func_name: ツール名
            args_dict: ツール引数
            session_id: セッションID（キャンセルチェック用）
            
        Returns:
            ツール実行結果
        """
        # ログ出力
        _log_tool_use(func_name, args_dict, self.verbose)
        
        # 開始通知
        if self.progress_callback:
            icon, name, arg_str, _ = _format_tool_log(func_name, args_dict)
            self.progress_callback(
                event_type="tool", 
                name=f"{icon} {name}", 
                detail=arg_str, 
                agent_name=self.agent_name, 
                parent_agent=self.parent_agent, 
                status="running",
                tool_name=func_name
            )
        
        if self.verbose:
            print(f"  args: {args_dict}")

        # キャンセルチェック
        if session_id:
            check_cancelled(session_id)

        # ループ検出
        allowed, block_msg = self.tool_tracker.check_and_record(func_name, args_dict)
        if not allowed:
            result = block_msg
        elif func_name in self.available_tools:
            try:
                raw_result = await _execute_tool_safely_async(self.available_tools[func_name], args_dict)
                result = _truncate_tool_output(raw_result, func_name)
            except Exception as e:
                result = f"Error executing {func_name}: {e}"
        else:
            result = f"Error: Tool {func_name} not found"

        # コンテキスト上限チェック
        result = self._update_context_usage(result)

        # 終了通知
        if self.progress_callback:
            icon, name, arg_str, _ = _format_tool_log(func_name, args_dict)
            self.progress_callback(
                event_type="tool", 
                name=f"{icon} {name}", 
                detail=arg_str, 
                agent_name=self.agent_name, 
                parent_agent=self.parent_agent, 
                status="completed",
                tool_name=func_name,
                result=result
            )

        return result

    async def run(self, user_input: str, history: Optional[List[Any]] = None, session_id: Optional[str] = None) -> str:
        """
        エージェントを実行する
        """
        if self.progress_callback:
            self.progress_callback(event_type="start", agent_name=self.name)

        # セッションIDを保存
        self._current_session_id = session_id

        # コンテキスト上限管理をリセット
        self._accumulated_tokens = 0
        self._tool_call_count = 0
        self._context_limit_reached = False
        
        # ツール呼び出しループ検出をリセット
        self.tool_tracker.reset()

        # キャンセルチェック
        if session_id:
            check_cancelled(session_id)

        # セマンティックメモリからの想起
        self._recall_results = []
        try:
            self._recall_results = self.semantic_memory.search(user_input, top_k=3)
            if self._recall_results and self.progress_callback:
                self.progress_callback(
                    event_type="recall",
                    agent_name=self.name,
                    results=self._recall_results
                )
        except Exception as e:
            if self.verbose:
                print(f"Warning: Semantic recall failed: {e}")

        if self.provider in (LLMProvider.OPENAI, LLMProvider.OPENROUTER, LLMProvider.ZAI):
            result = await self._run_openai(user_input, history, session_id=session_id)
        else:
            result = await self._run_gemini(user_input, history, session_id=session_id)

        # CostTrackerにコストを記録
        self._record_cost()

        if self.progress_callback:
            self.progress_callback(event_type="done", status="completed", agent_name=self.name)

        return result

    def _record_cost(self) -> None:
        """CostTrackerにコストを記録"""
        if not self.last_usage:
            return
        try:
            from moco.core.cost_tracker import get_cost_tracker, TokenUsage
            from moco.storage.usage_store import get_usage_store
            
            tracker = get_cost_tracker()
            usage_store = get_usage_store()
            
            usage = TokenUsage(
                input_tokens=self.last_usage.get("prompt_tokens", 0),
                output_tokens=self.last_usage.get("completion_tokens", 0),
            )
            # プロバイダ名を決定
            provider_name = "gemini" if self.provider == LLMProvider.GEMINI else "openai"
            if self.provider == LLMProvider.OPENROUTER:
                provider_name = "openrouter"
            elif self.provider == LLMProvider.ZAI:
                provider_name = "zai"

            session_id = getattr(self, "_current_session_id", None)

            record = tracker.record(
                provider=provider_name,
                model=self.model_name,
                usage=usage,
                agent_name=self.name,
                session_id=session_id,
            )
            
            # DBにも永続化
            usage_store.record_usage(
                provider=provider_name,
                model=self.model_name,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=record.cost_usd,
                session_id=session_id,
                agent_name=self.name
            )
        except Exception as e:
            if self.verbose:
                print(f"Warning: Failed to record cost: {e}")

    def get_metrics(self) -> Dict[str, Any]:
        """最新の実行メトリクスを取得"""
        return self.last_usage

    async def _run_openai(self, user_input: str, history: Optional[List[Any]] = None, session_id: Optional[str] = None) -> str:
        """OpenAI GPTでエージェントを実行"""
        if history is None:
            history = []

        # メッセージ構築
        messages = [{"role": "system", "content": self._get_system_prompt()}]

        # 履歴を追加
        for h in history:
            if isinstance(h, dict):
                role = h.get("role", "user")
                # Gemini形式 → OpenAI形式に変換
                if role == "model":
                    role = "assistant"
                # tool/function ロールはスキップ（一部モデルで未サポート）
                if role not in ("system", "user", "assistant"):
                    continue
                content = h.get("content", "")
                if not content and "parts" in h:
                    parts = h.get("parts", [])
                    content = " ".join(str(p) for p in parts)
                if content:  # 空メッセージはスキップ
                    messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_input})

        # ツール設定
        tools = self.openai_tools if self.openai_tools else None

        # max_iterations をコメントアウト: トークン上限で管理
        # iterations = 0
        # max_iterations = 20
        while True:
            if session_id:
                check_cancelled(session_id)
            
            # イテレーション警告はコメントアウト（トークン上限で管理）
            # remaining = max_iterations - iterations
            # if remaining <= 3 and remaining > 0:
            #     warning_msg = ...
            
            try:
                if self.stream:
                    # ストリーミングモード
                    # reasoning/thinking モデルの場合
                    if _is_reasoning_model(self.model_name):
                        extra_body = {}
                        
                        # OpenRouter の場合は reasoning パラメータを使用
                        # effort と max_tokens は同時に指定できない
                        if self.provider == LLMProvider.OPENROUTER:
                            extra_body["reasoning"] = {
                                "effort": "high"  # low, medium, high, xhigh
                            }
                        
                        create_kwargs = {
                            "model": self.model_name,
                            "messages": messages,
                            "tools": tools,
                            "stream": True,
                            "stream_options": {"include_usage": True},
                            "parallel_tool_calls": True,
                        }
                        
                        # OpenAI o1/o3 の場合は reasoning_effort を使用
                        if self.provider != LLMProvider.OPENROUTER:
                            create_kwargs["reasoning_effort"] = "medium"
                        
                        if extra_body:
                            create_kwargs["extra_body"] = extra_body
                        
                        response = await self.openai_client.chat.completions.create(**create_kwargs)
                    else:
                        response = await self.openai_client.chat.completions.create(
                            model=self.model_name,
                            messages=messages,
                            tools=tools,
                            temperature=0.7,
                            stream=True,
                            stream_options={"include_usage": True},
                            parallel_tool_calls=True,
                        )

                    # ストリーミングレスポンスを処理
                    collected_content = ""
                    collected_tool_calls = []
                    current_tool_call = None
                    # 思考テキストのバッファリング（GLM等の細かいチャンク対策）
                    reasoning_buffer = ""
                    reasoning_header_shown = False

                    async for chunk in response:
                        # usage情報の取得（最後のチャンクに含まれる）
                        if hasattr(chunk, "usage") and chunk.usage:
                            self.last_usage = {
                                "prompt_tokens": chunk.usage.prompt_tokens,
                                "completion_tokens": chunk.usage.completion_tokens,
                                "total_tokens": chunk.usage.total_tokens
                            }

                        delta = chunk.choices[0].delta if chunk.choices else None
                        if not delta:
                            continue

                        # reasoning/thinking コンテンツの処理
                        # OpenRouter: delta.reasoning または delta.reasoning_details
                        # OpenAI o1/o3: delta.reasoning_content
                        reasoning_text = None
                        # OpenRouter: reasoning フィールド（文字列）
                        if hasattr(delta, 'reasoning') and delta.reasoning:
                            reasoning_text = delta.reasoning
                        # OpenRouter: reasoning_details フィールド（配列）
                        elif hasattr(delta, 'reasoning_details') and delta.reasoning_details:
                            if isinstance(delta.reasoning_details, list):
                                for detail in delta.reasoning_details:
                                    # dict の場合
                                    if isinstance(detail, dict) and detail.get('text'):
                                        reasoning_text = detail['text']
                                        break
                                    # object の場合
                                    elif hasattr(detail, 'text') and detail.text:
                                        reasoning_text = detail.text
                                        break
                            else:
                                reasoning_text = str(delta.reasoning_details)
                        # OpenAI o1/o3: reasoning_content
                        elif hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                            reasoning_text = delta.reasoning_content
                        
                        if reasoning_text:
                            if self.progress_callback:
                                # Web UI経由: progress_callback でバッチ化して送信
                                self.progress_callback(
                                    event_type="thinking",
                                    content=reasoning_text,
                                    agent_name=self.name
                                )
                            else:
                                # CLI直接実行: verbose のときだけ思考過程を表示する
                                if self.verbose and not self.progress_callback:
                                    # バッファリングして句点/改行/一定文字数でフラッシュ
                                    reasoning_buffer += reasoning_text
                                    # ヘッダーを1回だけ表示
                                    if not reasoning_header_shown:
                                        _safe_stream_print("\n💭 [思考中...]\n")
                                        reasoning_header_shown = True
                                    # フラッシュ条件: 句点、改行、または80文字以上
                                    while len(reasoning_buffer) >= 80 or any(c in reasoning_buffer for c in '。\n'):
                                        # 句点か改行があればそこまで出力
                                        flush_pos = -1
                                        for i, c in enumerate(reasoning_buffer):
                                            if c in '。\n':
                                                flush_pos = i + 1
                                                break
                                        if flush_pos == -1 and len(reasoning_buffer) >= 80:
                                            flush_pos = 80
                                        if flush_pos > 0:
                                            _safe_stream_print(reasoning_buffer[:flush_pos])
                                            reasoning_buffer = reasoning_buffer[flush_pos:]
                                        else:
                                            break
                                else:
                                    # verbose でない場合は思考バッファを使わない
                                    reasoning_buffer = ""
                        # テキストコンテンツをストリーム出力
                        if delta.content:
                            if not self.progress_callback:
                                _safe_stream_print(delta.content)
                            collected_content += delta.content
                            self._partial_response = collected_content  # エラー時の復旧用
                            if self.progress_callback:
                                self.progress_callback(
                                    event_type="chunk",
                                    content=delta.content,
                                    agent_name=self.name
                                )

                        # ツール呼び出しの収集
                        if delta.tool_calls:
                            for tc_delta in delta.tool_calls:
                                if tc_delta.index is not None:
                                    # 新しいツール呼び出しまたは既存の更新
                                    while len(collected_tool_calls) <= tc_delta.index:
                                        collected_tool_calls.append({
                                            "id": "",
                                            "type": "function",
                                            "function": {"name": "", "arguments": ""}
                                        })

                                    tc = collected_tool_calls[tc_delta.index]
                                    if tc_delta.id:
                                        tc["id"] = tc_delta.id
                                    if tc_delta.function:
                                        if tc_delta.function.name:
                                            tc["function"]["name"] = tc_delta.function.name
                                        if tc_delta.function.arguments:
                                            tc["function"]["arguments"] += tc_delta.function.arguments

                    # 残りの思考バッファをフラッシュ（verbose のときだけ）
                    if reasoning_buffer and self.verbose and not self.progress_callback:
                        _safe_stream_print(reasoning_buffer)
                        reasoning_buffer = ""
                    if reasoning_header_shown and self.verbose and not self.progress_callback:
                        _safe_stream_print("\n[/思考]\n")

                    if collected_content and not self.progress_callback:
                        _safe_stream_print("\n")  # 改行

                    # ツール呼び出しがあるか確認
                    if collected_tool_calls and collected_tool_calls[0]["id"]:
                        # ツール呼び出しを処理
                        messages.append({
                            "role": "assistant",
                            "content": collected_content or "",
                            "tool_calls": collected_tool_calls
                        })

                        for tc in collected_tool_calls:
                            func_name = tc["function"]["name"]
                            try:
                                args_dict = json.loads(tc["function"]["arguments"])
                            except json.JSONDecodeError:
                                args_dict = {}

                            result = await self._execute_tool_with_tracking(func_name, args_dict, session_id)

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": str(result)
                            })
                        
                        # 80%超過時にコンテキスト圧縮
                        usage_ratio = self._accumulated_tokens / MAX_CONTEXT_TOKENS
                        if usage_ratio >= CONTEXT_WARNING_THRESHOLD:
                            compressor = ContextCompressor(
                                max_tokens=int(MAX_CONTEXT_TOKENS * CONTEXT_WARNING_THRESHOLD),
                                preserve_recent=10
                            )
                            messages, was_compressed = compressor.compress_if_needed(messages, self.provider)
                            if was_compressed:
                                self._accumulated_tokens = compressor.estimate_tokens(messages)
                                if self.verbose:
                                    print(f"\n🗜️ [Context compressed: {self._accumulated_tokens:,} tokens]")
                        
                        continue  # 次のイテレーション
                    else:
                        # 空の場合は部分応答を返す
                        if not collected_content and self._partial_response:
                            return self._partial_response
                        return collected_content
                else:
                    # 非ストリーミングモード
                    # reasoning モデル (o1/o3) の場合は reasoning_effort を使用
                    if _is_reasoning_model(self.model_name):
                        response = await self.openai_client.chat.completions.create(
                            model=self.model_name,
                            messages=messages,
                            tools=tools,
                            reasoning_effort="medium",  # thinking mode ON
                            parallel_tool_calls=True,
                        )
                    else:
                        response = await self.openai_client.chat.completions.create(
                            model=self.model_name,
                            messages=messages,
                            tools=tools,
                            temperature=0.7,
                            parallel_tool_calls=True,
                        )
                    # usage記録
                    if hasattr(response, "usage") and response.usage:
                        self.last_usage = {
                            "prompt_tokens": response.usage.prompt_tokens,
                            "completion_tokens": response.usage.completion_tokens,
                            "total_tokens": response.usage.total_tokens
                        }
            except Exception as e:
                return f"Error calling OpenAI API: {e}"

            if not self.stream:
                choice = response.choices[0]
                message = choice.message
            else:
                continue  # ストリーミングの場合は上で処理済み

            # ツール呼び出しの確認（非ストリーミング）
            if message.tool_calls:
                # アシスタントメッセージを追加
                messages.append({
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        for tc in message.tool_calls
                    ]
                })

                # ツール実行
                for tc in message.tool_calls:
                    func_name = tc.function.name
                    try:
                        args_dict = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args_dict = {}

                    result = await self._execute_tool_with_tracking(func_name, args_dict, session_id)

                    # ツール結果を追加
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(result)
                    })
                
                # 80%超過時にコンテキスト圧縮
                usage_ratio = self._accumulated_tokens / MAX_CONTEXT_TOKENS
                if usage_ratio >= CONTEXT_WARNING_THRESHOLD:
                    compressor = ContextCompressor(
                        max_tokens=int(MAX_CONTEXT_TOKENS * CONTEXT_WARNING_THRESHOLD),
                        preserve_recent=10
                    )
                    messages, was_compressed = compressor.compress_if_needed(messages, self.provider)
                    if was_compressed:
                        self._accumulated_tokens = compressor.estimate_tokens(messages)
                        if self.verbose:
                            print(f"[Context compressed: {self._accumulated_tokens:,} tokens]")
            else:
                # テキスト応答を返す
                return message.content or ""

        # max_iterations に達した場合
        if self._partial_response:
            return f"[最大イテレーション到達]\n{self._partial_response}"
        return "Error: Max iterations reached without response."

    async def _run_gemini(self, user_input: str, history: Optional[List[Any]] = None, session_id: Optional[str] = None) -> str:
        """Geminiでエージェントを実行"""
        if history is None:
            history = []

        # システムプロンプトの構築
        system_instruction = self._get_system_prompt()

        # ツール設定
        tools_config = None
        if self.tool_declarations:
            # Python関数をそのまま渡すと自動的にスキーマ生成される
            tools_config = [types.Tool(function_declarations=self.tool_declarations)]

        messages = []
        # 履歴の追加（dict形式をContent形式に変換）
        for h in history:
            if isinstance(h, dict):
                role = h.get("role", "user")
                parts = h.get("parts", [])
                if not parts and "content" in h:
                    parts = [h["content"]]
                # partsをPartオブジェクトに変換
                part_objects = []
                for p in parts:
                    if isinstance(p, str):
                        part_objects.append(types.Part(text=p))
                    else:
                        part_objects.append(p)
                messages.append(types.Content(role=role, parts=part_objects))
            else:
                # 既にContent形式の場合
                messages.append(h)

        messages.append(types.Content(role="user", parts=[types.Part(text=user_input)]))

        # 設定（thinking mode を有効化）
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools_config,
            temperature=0.7,
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=32768,  # API仕様上の最小値
            ),
        )

        while True:
            if session_id:
                check_cancelled(session_id)

            try:
                if self.stream:
                    # ストリーミングモード
                    response_stream = self.client.models.generate_content_stream(
                        model=self.model_name,
                        contents=messages,
                        config=config
                    )

                    collected_text = ""
                    collected_parts = []
                    function_calls = []

                    for chunk in response_stream:
                        # usage情報の取得
                        if chunk.usage_metadata:
                            self.last_usage = {
                                "prompt_tokens": chunk.usage_metadata.prompt_token_count,
                                "completion_tokens": chunk.usage_metadata.candidates_token_count,
                                "total_tokens": chunk.usage_metadata.total_token_count
                            }

                        if not chunk.candidates:
                            continue

                        candidate = chunk.candidates[0]
                        if not candidate.content:
                            continue

                        for part in candidate.content.parts or []:
                            # 思考プロセスの表示 (verbose モードのみ)
                            if hasattr(part, 'thought') and part.thought and part.text:
                                if self.progress_callback:
                                    self.progress_callback(
                                        event_type="thinking",
                                        content=part.text,
                                        agent_name=self.name
                                    )
                                elif self.verbose:
                                    thought_text = f"\n💭 [思考中...]\n{part.text}\n[/思考]\n"
                                    _safe_stream_print(thought_text)
                                continue
                            if part.text:
                                if not self.progress_callback:
                                    _safe_stream_print(part.text)
                                collected_text += part.text
                                self._partial_response = collected_text
                                collected_parts.append(part)
                                if self.progress_callback:
                                    self.progress_callback(
                                        event_type="chunk",
                                        content=part.text,
                                        agent_name=self.name
                                    )
                            if part.function_call:
                                function_calls.append(part.function_call)
                                collected_parts.append(part)

                    if collected_text and not self.progress_callback:
                        _safe_stream_print("\n")

                    if function_calls:
                        messages.append(types.Content(role="model", parts=collected_parts))
                        tool_responses = []
                        for fc in function_calls:
                            func_name = fc.name
                            args = fc.args
                            args_dict = {}
                            if args:
                                if hasattr(args, "items"):
                                    args_dict = {k: v for k, v in args.items()}
                                elif isinstance(args, dict):
                                    args_dict = args

                            result = await self._execute_tool_with_tracking(func_name, args_dict, session_id)

                            tool_responses.append(
                                types.Part(
                                    function_response=types.FunctionResponse(
                                        name=func_name,
                                        response={"result": _ensure_jsonable(result)}
                                    )
                                )
                            )
                        messages.append(types.Content(role="tool", parts=tool_responses))
                        continue
                    else:
                        return collected_text

                else:
                    # 非ストリーミングモード
                    response = self.client.models.generate_content(
                        model=self.model_name,
                        contents=messages,
                        config=config
                    )
                    
                    if response.usage_metadata:
                        self.last_usage = {
                            "prompt_tokens": response.usage_metadata.prompt_token_count,
                            "completion_tokens": response.usage_metadata.candidates_token_count,
                            "total_tokens": response.usage_metadata.total_token_count
                        }

                    if not response.candidates:
                        return "Error: No response candidates from Gemini."

                    candidate = response.candidates[0]
                    message = candidate.content
                    if not message:
                        return "Error: Empty response content from Gemini."

                    messages.append(message)
                    
                    for part in message.parts or []:
                        if hasattr(part, 'thought') and part.thought and part.text:
                            if self.progress_callback:
                                self.progress_callback(
                                    event_type="thinking",
                                    content=part.text,
                                    agent_name=self.name
                                )
                            elif self.verbose:
                                print(f"\n💭 [思考中...]\n{part.text}\n[/思考]")

                    function_calls = [p.function_call for p in message.parts if p.function_call]
                    if function_calls:
                        tool_responses = []
                        for fc in function_calls:
                            func_name = fc.name
                            args = fc.args
                            args_dict = {}
                            if args:
                                if hasattr(args, "items"):
                                    args_dict = {k: v for k, v in args.items()}
                                elif isinstance(args, dict):
                                    args_dict = args

                            result = await self._execute_tool_with_tracking(func_name, args_dict, session_id)

                            tool_responses.append(
                                types.Part(
                                    function_response=types.FunctionResponse(
                                        name=func_name,
                                        response={"result": _ensure_jsonable(result)}
                                    )
                                )
                            )
                        messages.append(types.Content(role="tool", parts=tool_responses))
                        continue
                    else:
                        full_text = "".join(p.text for p in message.parts if p.text)
                        return full_text

            except Exception as e:
                import traceback
                traceback.print_exc()
                return f"Error calling Gemini API: {e}"
