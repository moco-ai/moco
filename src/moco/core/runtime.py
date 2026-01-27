# ruff: noqa: E402
import asyncio
import os
import json
import inspect
import hashlib
from ..cancellation import check_cancelled, OperationCancelled
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Callable, get_type_hints
from collections import defaultdict

from ..tools.skill_loader import SkillConfig
from ..utils.json_parser import SmartJSONParser


class ToolCallTracker:
    """Tracker to detect and prevent loops of the same tool calls"""
    
    HASH_THRESHOLD = 100  # Hash the argument string if it exceeds this length
    
    def __init__(self, max_repeats: int = 3, window_size: int = 10):
        """
        Args:
            max_repeats: Maximum number of times the same call is allowed
            window_size: Number of recent calls to check
        """
        self.history: List[str] = []
        self.max_repeats = max_repeats
        self.window_size = window_size
        self.blocked_calls: Dict[str, int] = defaultdict(int)
    
    def _make_key(self, tool_name: str, args: dict) -> str:
        """Generate a unique key for the tool call (hashed if arguments are large)"""
        try:
            args_str = json.dumps(args, sort_keys=True, default=str)
            # Use SHA-256 hash if arguments exceed the threshold
            if len(args_str) > self.HASH_THRESHOLD:
                args_hash = hashlib.sha256(args_str.encode()).hexdigest()
                return f"{tool_name}:hash:{args_hash}"
            return f"{tool_name}:{args_str}"
        except (TypeError, ValueError):
            return f"{tool_name}:{str(args)}"
    
    def check_and_record(self, tool_name: str, args: dict) -> tuple[bool, str]:
        """
        Check the tool call and record it in the history.
        
        Returns:
            (allowed: bool, message: str)
            - allowed=True: Execution allowed
            - allowed=False: Loop detected, execution blocked
        """
        call_key = self._make_key(tool_name, args)
        
        # Count the same key in recent calls
        recent = self.history[-self.window_size:] if len(self.history) >= self.window_size else self.history
        repeat_count = sum(1 for h in recent if h == call_key)
        
        if repeat_count >= self.max_repeats:
            self.blocked_calls[call_key] += 1
            return False, (
                f"⚠️ Loop detected: {tool_name} was called {repeat_count} times with the same arguments.\n"
                f"This tool call has been blocked.\n"
                f"Please try a different approach or use different arguments."
            )
        
        self.history.append(call_key)
        return True, ""
    
    def reset(self):
        """Reset history"""
        self.history.clear()
        self.blocked_calls.clear()

# Gemini
from google import genai
from google.genai import types

# OpenAI
try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

from ..tools.discovery import AgentConfig
from ..storage.semantic_memory import SemanticMemory
from .context_compressor import ContextCompressor

# For tool usage logs
MAX_ARG_LEN = 40  # Maximum number of characters for arguments

class StreamPrintState:
    """Class to manage stdout state (resettable during testing)"""
    broken = False
    
    @classmethod
    def reset(cls):
        """For testing: reset state"""
        cls.broken = False


def _safe_stream_print(text: str) -> None:
    """Safe print for streaming display.

    Prevents BrokenPipeError / OSError(errno=32) when stdout is closed (e.g., during Web UI execution),
    by suppressing subsequent standard output and continuing the process.
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

# Icons and colors by tool type
TOOL_STYLES = {
    # File operations
    "read_file": ("📖", "cyan"),
    "write_file": ("📝", "green"),
    "edit_file": ("✏️", "yellow"),
    # Execution
    "execute_bash": ("⚡", "magenta"),
    # Delegation
    "delegate_to_agent": ("👤", "blue"),
    # Search
    "websearch": ("🔍", "cyan"),
    "webfetch": ("🌐", "cyan"),
    "grep": ("🔎", "dim"),
    "codebase_search": ("🔍", "cyan"),
    # Directory
    "list_dir": ("📁", "dim"),
    "glob_search": ("📂", "dim"),
    # Calculation
    "calculate_tax": ("🧮", "green"),
    # Default
    "_default": ("🔧", "dim"),
}

def _format_tool_log(tool_name: str, args: dict) -> tuple:
    """Format tool log. Returns (icon, name, arg_str, color)"""
    style = TOOL_STYLES.get(tool_name, TOOL_STYLES["_default"])
    icon, color = style

    # Extract arguments
    arg_str = ""

    if tool_name in ("read_file", "write_file", "edit_file"):
        path = args.get("path") or args.get("file_path") or ""
        # Extract only the filename
        if path and "/" in path:
            arg_str = path.split("/")[-1]
        else:
            arg_str = path or ""
        # Also display offset/limit (if used)
        offset = args.get("offset")
        limit = args.get("limit")
        if offset or limit:
            start = int(offset) if offset else 1
            end = start + (int(limit) if limit else 0) - 1 if limit else "end"
            arg_str += f" [L{start}-{end}]"

    elif tool_name == "execute_bash":
        cmd = args.get("command") or ""
        # Only the first command
        arg_str = cmd.split()[0] if cmd and cmd.split() else (cmd or "")
        if cmd and len(cmd) > len(arg_str) + 5:
            arg_str += " ..."

    elif tool_name == "delegate_to_agent":
        name = args.get('agent_name') or ''
        arg_str = name if name.startswith('@') else f"@{name}"

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
        # Only the domain
        if url and "://" in url:
            arg_str = url.split("://")[1].split("/")[0]
        else:
            arg_str = url[:25] if url else ""

    else:
        # Others: first argument
        for k, v in list(args.items())[:1]:
            v_str = str(v)
            if len(v_str) > 25:
                v_str = v_str[:25] + "..."
            arg_str = v_str
            break

    # Length limit
    if len(arg_str) > MAX_ARG_LEN:
        arg_str = arg_str[:MAX_ARG_LEN - 3] + "..."

    return icon, tool_name, arg_str, color

try:
    from rich.console import Console
    _tool_console = Console()

    def _log_tool_use(tool_name: str, args: dict = None, verbose: bool = False):
        """Log tool usage concisely"""
        if verbose:
            pass  # In verbose mode, detailed logs are output separately
        else:
            icon, name, arg_str, color = _format_tool_log(tool_name, args or {})
            # Align tool names to a fixed width
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
    Validate the types of function arguments and convert them if possible.
    """
    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except (TypeError, NameError, ValueError):
        hints = {}

    validated_args = {}
    missing_required: List[str] = []
    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        
        if param_name in args:
            val = args[param_name]
            expected_type = hints.get(param_name)
            
            if expected_type:
                # Simple type conversion/check
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
            # If there's a default value, do nothing (the default will be used in func(**validated_args))
            pass
        else:
            # Required parameter is missing
            missing_required.append(param_name)
            
    if missing_required:
        raise ValueError(f"missing required arguments: {', '.join(missing_required)}")

    return validated_args


def _execute_tool_safely(func: Callable, args: Dict[str, Any]) -> Any:
    """
    Execute tool safely. If it's an async function, execute it synchronously.
    """
    valid_args = _validate_arguments(func, args)
    result = func(**valid_args)
    
    # Execute synchronously if it's an async function
    if asyncio.iscoroutine(result):
        try:
            # Python 3.10+: Check for a running loop using get_running_loop()
            asyncio.get_running_loop()
            # Execute in a new thread if within a running loop
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, result)
                result = future.result(timeout=60)
        except RuntimeError:
            # Use asyncio.run() if no loop is running
            result = asyncio.run(result)
    
    return result


async def _execute_tool_safely_async(func: Callable, args: Dict[str, Any]) -> Any:
    """
    Execute tool safely (async version).
    - Resolve async tool / coroutine return values with await
    - Execute synchronous tools with to_thread to avoid blocking the event loop
    """
    valid_args = _validate_arguments(func, args)

    # First, normal call
    if asyncio.iscoroutinefunction(func):
        result = await func(**valid_args)
    else:
        result = await asyncio.to_thread(func, **valid_args)

    # Resolve coroutine return value just in case
    if asyncio.iscoroutine(result):
        result = await result
    return result


# Maximum number of characters for tool output (saved to temporary file if exceeded)
MAX_TOOL_OUTPUT_CHARS = 50000
_TEMP_OUTPUT_DIR = "/tmp/moco_tool_outputs"

# Context limit management (within one agent execution)
MAX_CONTEXT_TOKENS = 150000      # Upper limit for input context (approx. 150K tokens)
# MAX_TOOL_CALLS = 15            # Commented out: Managed by ContextCompressor
CONTEXT_WARNING_THRESHOLD = 0.8  # Warning/compression triggered at 80%


def _gemini_messages_to_dict(messages: List[Any]) -> List[Dict[str, Any]]:
    """Convert Gemini's types.Content to Dict format"""
    result = []
    for msg in messages:
        if hasattr(msg, 'role') and hasattr(msg, 'parts'):
            # types.Content object
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
    """Convert Dict format to Gemini's types.Content"""
    result = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        # Convert model -> assistant (Gemini uses model)
        if role == "assistant":
            role = "model"
        result.append(types.Content(role=role, parts=[types.Part(text=content)]))
    return result


# Common rules for all agents (automatically injected into system prompt)
COMMON_AGENT_RULES = """
## 🔧 ツール呼び出しのルール

`delegate_to_agent` でサブエージェントに委譲するときは、**必ずツール呼び出し（JSON形式）で実行**してください。
Markdown で「delegate_to_agent: @name」と書くのではなく、実際にツールを呼び出してください。

## 🧠 Skills（スキル）ツール

このシステムでは、スキルは **自動ロードされません**。必要なときにツールで明示的にロードしてください。

- `search_skills(query: str, include_remote: bool = True)`: スキル候補を検索（ローカル/リモート）。
- `load_skill(skill_name: str, source: str = "auto")`: スキル本文（ガイド/知識）をロードして参照。
- `list_loaded_skills()`: 現在ロード済みのスキル一覧を表示。
- `execute_skill(skill_name: str, tool_name: str, arguments: dict)`: ロジック型スキル（JS/TS/Python）の **宣言済みツール**を実行（`SKILL.md` の frontmatter `tools:` に定義されているもののみ）。

注意:
- ロード済みスキルのキャッシュは **ユーザー入力ごとにクリアされる**ため、毎ターン必要なら再度 `load_skill` してください。

## ⛔ ツール呼び出し上限時のルール

### When you reach the limit yourself
When the "⛔ Tool call limit reached" message is displayed:
1. Summarize the work done so far
2. Return the remaining tasks in the following JSON format:

```json
{
  "status": "interrupted",
  "completed": ["Task 1 completed", "Task 2 completed"],
  "remaining": ["Remaining task 1", "Remaining task 2"],
  "context": "Information needed for continuation (target files, current state, etc.)"
}
```

### When receiving an interruption from a delegate
When receiving a response containing `"status": "interrupted"` from a delegate agent:
1. Check the content of `remaining`
2. Delegate again to the same agent with the `remaining` task and `context`
3. Repeat until complete

## 📄 Rules for truncated output

When the following message is displayed in the tool output:
- `⚠️ OUTPUT TRUNCATED` or `Content truncated`
- `👉 NEXT STEP:` instructions starting with

**Be sure to execute the instructed command to read the rest.**
- Do not repeat the same request
- Use the displayed path, offset, and limit as is
- Repeat until you have finished reading the rest
"""

def _estimate_tokens(text: str) -> int:
    """Estimate number of tokens (Simple: 4 chars ≒ 1 token)"""
    return len(text) // 4

def _truncate_tool_output(result: Any, tool_name: str) -> str:
    """
    If the tool output is too long, save it to a temporary file and return a reference.
    """
    if result is None:
        return "No output"
    
    result_str = str(result)
    
    if len(result_str) <= MAX_TOOL_OUTPUT_CHARS:
        return result_str
    
    # Save to a temporary file if too long
    import os
    import time
    
    os.makedirs(_TEMP_OUTPUT_DIR, exist_ok=True)
    filename = f"{tool_name}_{int(time.time() * 1000)}.txt"
    filepath = os.path.join(_TEMP_OUTPUT_DIR, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(result_str)
    
    # Display the beginning and refer to the file for the rest
    preview = result_str[:500]
    total_lines = result_str.count('\n') + 1
    total_chars = len(result_str)
    
    return (
        f"{preview}\n\n"
        f"⚠️ OUTPUT TRUNCATED ⚠️\n"
        f"Total: {total_chars:,} chars, {total_lines:,} lines\n"
        f"Full output saved to: {filepath}\n\n"
        f"👉 NEXT STEP: Execute the following to read the rest:\n"
        f"   read_file(path=\"{filepath}\", offset=1, limit=10000)\n"
    )


def _ensure_jsonable(value: Any) -> Any:
    """Return non-JSON-serializable values (e.g., coroutines) as strings."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


# LLM Provider Settings
class LLMProvider:
    GEMINI = "gemini"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    ZAI = "zai"  # Z.ai GLM-4.7


def _is_reasoning_model(model_name: str) -> bool:
    """Determine if the model supports reasoning/thinking
    
    - OpenAI: o1, o3, o4 series
    - Gemini (via OpenRouter): gemini-2.5, gemini-3 series
    """
    model_lower = model_name.lower()
    # OpenAI reasoning models
    openai_patterns = ["o1", "o3", "o4"]
    # Gemini thinking models (via OpenRouter: google/gemini-...)
    gemini_patterns = ["gemini-2.5", "gemini-3", "gemini-2.0-flash-thinking"]
    
    all_patterns = openai_patterns + gemini_patterns
    return any(pattern in model_lower for pattern in all_patterns)


def _python_type_to_schema(py_type) -> Dict[str, Any]:
    """Convert Python type to JSON schema"""
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
    """Convert Python function to OpenAI tool format"""
    sig = inspect.signature(func)
    docstring = func.__doc__ or ""
    # Use entire docstring as description (to convey format examples to LLM)
    description = docstring.strip() if docstring else f"{tool_name} function"

    properties = {}
    required = []

    try:
        hints = get_type_hints(func)
    except (TypeError, NameError, ValueError):
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
    """Convert Python function to FunctionDeclaration"""
    sig = inspect.signature(func)
    docstring = func.__doc__ or ""

    # Use the entire docstring as the description (to convey formatting examples, etc., to the LLM)
    description = docstring.strip() if docstring else f"{tool_name} function"

    # Build parameter schema
    properties = {}
    required = []

    try:
        hints = get_type_hints(func)
    except (TypeError, NameError, ValueError):
        hints = {}

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue

        # Use type annotation if available
        param_type = hints.get(param_name, str)
        schema = _python_type_to_schema(param_type)

        # Extract parameter descriptions from docstring (Args: section)
        param_desc = f"Parameter: {param_name}"
        if f"{param_name}" in docstring:
            # Simple extraction
            lines = docstring.split("\n")
            for line in lines:
                if param_name in line and ":" in line:
                    param_desc = line.split(":", 1)[-1].strip()
                    break

        properties[param_name] = {**schema, "description": param_desc}

        # Required if there's no default value
        if param.default == inspect.Parameter.empty:
            required.append(param_name)

    # The parameters argument requires a Schema type, but passing a dict structure is often converted internally
    # If an error occurs, it might be necessary to wrap it with types.Schema(**...)
    # For now, pass it as a dictionary for compatibility
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
        skills: Optional[List[SkillConfig]] = None,
        memory_service = None,
        system_prompt_override: Optional[str] = None
    ):
        self.config = config
        self.tool_map = tool_map
        self.agent_name = agent_name
        self.name = name or agent_name
        self.verbose = verbose
        self.progress_callback = progress_callback
        self.parent_agent = parent_agent
        self.skills: List[SkillConfig] = skills or []
        self.memory_service = memory_service
        self.system_prompt_override = system_prompt_override
        
        # Memory context (dynamically updated in run())
        self._memory_context = ""
        self._recall_results = []
        
        # Tool call loop detection
        self.tool_tracker = ToolCallTracker(max_repeats=3, window_size=10)
        # Track repeated invalid tool calls (e.g. missing required arguments)
        self._invalid_tool_call_counts: Dict[str, int] = defaultdict(int)
        
        # Initialization of semantic memory
        self.semantic_memory = semantic_memory
        if not self.semantic_memory:
            from pathlib import Path
            db_path = os.getenv("SEMANTIC_DB_PATH", str(Path.cwd() / "data" / "semantic.db"))
            self.semantic_memory = SemanticMemory(db_path=db_path)

        # Determine provider (environment variable, argument, or auto-selection)
        from .llm_provider import get_available_provider
        if provider:
            self.provider = provider
        elif os.environ.get("LLM_PROVIDER"):
            self.provider = os.environ.get("LLM_PROVIDER")
        else:
            self.provider = get_available_provider()

        # Determine model name
        if model:
            self.model_name = model
        elif self.provider == LLMProvider.OPENROUTER:
            self.model_name = os.environ.get("OPENROUTER_MODEL", "google/gemini-3-flash-preview")
        elif self.provider == LLMProvider.OPENAI:
            self.model_name = os.environ.get("OPENAI_MODEL", "gpt-5.1")
        elif self.provider == LLMProvider.ZAI:
            self.model_name = os.environ.get("ZAI_MODEL", "glm-4.7")
        else:
            self.model_name = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")

        # Initialization of client
        if self.provider == LLMProvider.OPENROUTER:
            # OpenRouter is an OpenAI-compatible API
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
            # Z.ai GLM-4.7 (OpenAI-compatible API)
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

        # Streaming configuration
        self.stream = stream
        
        # Partial response (to save in case of error)
        self._partial_response = ""

        # Context limit management (reset within one run())
        self._accumulated_tokens = 0
        self._tool_call_count = 0
        self._context_limit_reached = False

        # For metrics recording
        self.last_usage: Dict[str, Any] = {}

        # Preparation of tools
        self.available_tools = {}
        self.tool_declarations = []  # For Gemini
        self.openai_tools = []       # For OpenAI

        self._prepare_tools()

    def _prepare_tools(self):
        """Prepare enabled tools.
        
        Tool selection rules:
        - tools: 省略 or 空 → 全ての基礎ツール
        - tools: ["*", ...] → 全ての基礎ツール + 追加指定
        - tools: [a, b] → a, b のみ（厳密ホワイトリスト）
        """
        # Determine which tools to enable
        if not self.config.tools or "*" in self.config.tools:
            # Empty or contains "*" → enable all base tools
            tools_to_enable = set(self.tool_map.keys())
            # Also add any explicitly listed tools (for non-base tools)
            if self.config.tools:
                tools_to_enable.update(self.config.tools)
            tools_to_enable.discard("*")  # Remove the wildcard itself
        else:
            # Explicit whitelist
            tools_to_enable = set(self.config.tools)

        for tool_name in tools_to_enable:
            if tool_name in self.tool_map:
                func = self.tool_map[tool_name]
                self.available_tools[tool_name] = func

                # Gemini FunctionDeclaration
                declaration = _func_to_declaration(func, tool_name)
                self.tool_declarations.append(declaration)

                # OpenAI tool definition
                openai_tool = _func_to_openai_tool(func, tool_name)
                self.openai_tools.append(openai_tool)
            else:
                if self.verbose:
                    print(f"Warning: Tool '{tool_name}' not found in provided tool_map")

    def _update_context_usage(self, result: str) -> str:
        """
        Accumulate tokens from tool results and perform limit checks.
        Return result with warning or limit message appended.
        """
        result_tokens = _estimate_tokens(result)
        self._accumulated_tokens += result_tokens
        self._tool_call_count += 1
        
        usage_ratio = self._accumulated_tokens / MAX_CONTEXT_TOKENS
        
        # Over 100%: Instruction for forced termination
        if usage_ratio >= 1.0:
            self._context_limit_reached = True
            return (
                f"{result}\n\n"
                f"⛔ **Context limit reached** ({self._accumulated_tokens:,} / {MAX_CONTEXT_TOKENS:,} tokens)\n"
                f"Do not call any more tools and complete the response with the current information."
            )
        
        # Tool call limit - Commented out: Managed by ContextCompressor
        # if self._tool_call_count >= MAX_TOOL_CALLS:
        #     self._context_limit_reached = True
        #     return (
        #         f"{result}\n\n"
        #         f"⛔ **Tool call limit reached** ({self._tool_call_count} / {MAX_TOOL_CALLS} calls)\n"
        #         f"Do not call any more tools and complete the response with the current information."
        #     )
        
        # 80% warning
        if usage_ratio >= CONTEXT_WARNING_THRESHOLD:
            remaining = MAX_CONTEXT_TOKENS - self._accumulated_tokens
            return (
                f"{result}\n\n"
                f"⚠️ Context {usage_ratio*100:.0f}% in use "
                f"(Approx. {remaining:,} tokens remaining)\n"
                f"Please keep tool calls to a minimum."
            )
        
        return result

    def _get_system_prompt(self) -> str:
        """Retrieve system prompt and insert context information"""
        # JST (UTC+9)
        jst = timezone(timedelta(hours=9))
        now_dt = datetime.now(jst)
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S (JST)")

        # Construction of context information
        context_header = f"---\n[Current Context]\nTimestamp: {now_str}\n"
        
        # Addition of recall results (semantic memory)
        if hasattr(self, "_recall_results") and self._recall_results:
            context_header += "\n[Related Knowledge/Past Incidents]\n"
            for i, res in enumerate(self._recall_results):
                content = res.get('content', '')
                # Truncate if too long
                if len(content) > 1000:
                    content = content[:1000] + "..."
                context_header += f"- Knowledge {i+1}:\n{content}\n"
        
        # MemoryService からの記憶コンテキスト追加
        if hasattr(self, "_memory_context") and self._memory_context:
            context_header += f"\n{self._memory_context}\n"
        
        context_header += "---\n\n"

        prompt = self.system_prompt_override or self.config.system_prompt

        # Injection of Skills
        if self.skills:
            skills_section = "\n\n## Skills\n\n"
            for i, skill in enumerate(self.skills, 1):
                skills_section += f"### Skill: {skill.name} (v{skill.version})\n"
                skills_section += f"{skill.description}\n"
                if skill.allowed_tools:
                    skills_section += f"\n**Allowed Tools**: {', '.join(skill.allowed_tools)}\n"
                skills_section += f"\n{skill.content}\n\n"
            prompt += skills_section

        # Placeholder replacement
        # May overlap with header, but addresses requests to embed date/time at specific locations in the prompt
        if "{{CURRENT_DATETIME}}" in prompt:
            prompt = prompt.replace("{{CURRENT_DATETIME}}", now_str)
        
        # Session context (injected only if {{SESSION_CONTEXT}} is used in md)
        if "{{SESSION_CONTEXT}}" in prompt:
            session_context = self._build_session_context()
            prompt = prompt.replace("{{SESSION_CONTEXT}}", session_context)
        
        # Agent stats (injected only if {{AGENT_STATS}} is used in md)
        if "{{AGENT_STATS}}" in prompt:
            agent_stats = self._build_agent_stats()
            prompt = prompt.replace("{{AGENT_STATS}}", agent_stats)

        # Add common rules at the end
        return context_header + prompt + "\n\n" + COMMON_AGENT_RULES
    
    def _build_session_context(self) -> str:
        """Construct current session context"""
        try:
            from ..tools.stats import get_session_stats
            return get_session_stats()
        except Exception as e:
            return f"(Session context retrieval error: {e})"
    
    def _build_agent_stats(self) -> str:
        """Construct agent stats"""
        try:
            from ..tools.stats import get_agent_stats
            return get_agent_stats(days=7)
        except Exception as e:
            return f"(Agent stats retrieval error: {e})"

    async def _execute_tool_with_tracking(
        self, 
        func_name: str, 
        args_dict: Dict[str, Any], 
        session_id: Optional[str] = None
    ) -> str:
        """Common processing for tool execution and tracking
        
        Args:
            func_name: Tool name
            args_dict: Tool arguments
            session_id: Session ID (for cancellation check)
            
        Returns:
            Tool execution result
        """
        # Cancellation check
        if session_id:
            check_cancelled(session_id)

        # Validate required args before logging/loop-detection (Cursor-like behavior)
        # - Avoid noisy "📝 write_file" lines when the model emits empty args
        # - Do not record invalid calls in loop-detection history
        if func_name in self.available_tools:
            try:
                _validate_arguments(self.available_tools[func_name], args_dict)
            except ValueError as e:
                # Count invalid calls and return a stronger, structured message to prevent loops
                self._invalid_tool_call_counts[func_name] += 1
                count = self._invalid_tool_call_counts[func_name]
                missing_msg = str(e)

                # Provide a JSON template that the model can directly fill.
                template = None
                if func_name == "write_file":
                    template = (
                        '{\n'
                        '  "path": "path/to/file.ext",\n'
                        '  "content": "file contents with \\\\n escaped newlines",\n'
                        '  "overwrite": false\n'
                        '}'
                    )
                elif func_name == "edit_file":
                    template = (
                        '{\n'
                        '  "path": "path/to/file.ext",\n'
                        '  "old_string": "include 3-5 lines before and after (use \\\\n for newlines)",\n'
                        '  "new_string": "replacement text (use \\\\n for newlines)"\n'
                        '}'
                    )

                if count >= 2:
                    # Escalate to stop repeated invalid tool calls
                    result = (
                        f"Error executing {func_name}: {missing_msg}\n"
                        f"STOP: You are repeating an invalid tool call ({count} times).\n"
                        f"Do NOT call `{func_name}` again until you have a complete JSON object with all required keys.\n"
                        f"Rebuild the arguments, then call `{func_name}` exactly once.\n"
                    )
                else:
                    result = (
                        f"Error executing {func_name}: {missing_msg}\n"
                        f"Fix: call `{func_name}` with a single complete JSON object including all required keys.\n"
                    )

                if template:
                    result += f"\nJSON template:\n{template}\n"
                # Cursor-like: do not emit any tool UI events for invalid calls.
                # (Showing a tool row like "🛠️ write_file" is noisy and looks like a real execution.)
                return self._update_context_usage(result)

        # Log output (only after validation)
        #
        # In streaming contexts (CLI `moco chat`, Web UI), we already emit tool status via
        # progress_callback. Printing tool-start lines directly here can interleave with
        # streamed assistant text and "break" the CLI output formatting.
        if not self.progress_callback:
            _log_tool_use(func_name, args_dict, self.verbose)

        # Start notification
        if self.progress_callback:
            icon, name, arg_str, _ = _format_tool_log(func_name, args_dict)
            self.progress_callback(
                event_type="tool",
                name=f"{icon} {name}",
                detail=arg_str,
                agent_name=self.agent_name,
                parent_agent=self.parent_agent,
                status="running",
                tool_name=func_name,
            )

        if self.verbose:
            print(f"  args: {args_dict}")

        # Loop detection
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

        # Context limit check
        result = self._update_context_usage(result)
        
        # Memory: Record tool execution event
        if self.memory_service and session_id:
            try:
                is_error = result.startswith("Error") if isinstance(result, str) else False
                await asyncio.to_thread(
                    self.memory_service.record_task_run_event,
                    run_id=session_id,
                    tool_name=func_name,
                    params=args_dict,
                    result={"output": result[:500] if isinstance(result, str) else str(result)[:500]},
                    success=not is_error,
                    error_type="tool_error" if is_error else None,
                )
            except Exception:
                pass  # Don't fail on memory errors

        # End notification
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
        Execute the agent
        """
        if self.progress_callback:
            self.progress_callback(event_type="start", agent_name=self.name)

        # Save session ID
        self._current_session_id = session_id

        # Set session for todo tool if available
        if session_id:
            try:
                from moco.tools import todo
                todo.set_current_session(session_id)
            except (ImportError, AttributeError):
                pass

        # Reset context limit management
        self._accumulated_tokens = 0
        self._tool_call_count = 0
        self._context_limit_reached = False
        
        # Reset tool call loop detection
        self.tool_tracker.reset()

        # Cancellation check
        if session_id:
            check_cancelled(session_id)

        # Recall from semantic memory
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

        # MemoryService からの記憶コンテキスト取得
        self._memory_context = ""
        if self.memory_service:
            try:
                self._memory_context = self.memory_service.get_context_prompt(user_input)
                if self._memory_context and self.verbose:
                    print(f"[Memory] Retrieved context: {len(self._memory_context)} chars")
            except Exception as e:
                if self.verbose:
                    print(f"Warning: Memory recall failed: {e}")

        if self.provider in (LLMProvider.OPENAI, LLMProvider.OPENROUTER, LLMProvider.ZAI):
            result = await self._run_openai(user_input, history, session_id=session_id)
        else:
            result = await self._run_gemini(user_input, history, session_id=session_id)

        # Record cost in CostTracker
        self._record_cost()

        if self.progress_callback:
            self.progress_callback(event_type="done", status="completed", agent_name=self.name)

        return result

    def _record_cost(self) -> None:
        """Record cost in CostTracker"""
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
            # Determine provider name
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
            
            # Persist to DB as well
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
        """Get the latest execution metrics"""
        return self.last_usage

    async def _run_openai(self, user_input: str, history: Optional[List[Any]] = None, session_id: Optional[str] = None) -> str:
        """Execute agent with OpenAI GPT"""
        if history is None:
            history = []

        # Message construction
        messages = [{"role": "system", "content": self._get_system_prompt()}]

        # Add history
        for h in history:
            if isinstance(h, dict):
                role = h.get("role", "user")
                # Convert Gemini format -> OpenAI format
                if role == "model":
                    role = "assistant"
                # Skip tool/function roles (unsupported by some models)
                if role not in ("system", "user", "assistant"):
                    continue
                content = h.get("content", "")
                if not content and "parts" in h:
                    parts = h.get("parts", [])
                    content = " ".join(str(p) for p in parts)
                if content:  # Skip empty messages
                    messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_input})

        # Tool settings
        tools = self.openai_tools if self.openai_tools else None

        # Z.ai doesn't support tool_stream in streaming mode for glm-4.7
        # Force non-streaming when using tools to ensure tool calls work correctly
        use_stream = self.stream
        if self.provider == LLMProvider.ZAI and tools:
            use_stream = False

        # Commented out max_iterations: managed by token limit
        # iterations = 0
        # max_iterations = 20
        while True:
            if session_id:
                check_cancelled(session_id)
            
            # Iteration warnings commented out (managed by token limit)
            # remaining = max_iterations - iterations
            # if remaining <= 3 and remaining > 0:
            #     warning_msg = ...
            
            try:
                if use_stream:
                    # Streaming mode
                    # In case of reasoning/thinking model
                    if _is_reasoning_model(self.model_name):
                        extra_body = {}
                        
                        # Use reasoning parameter for OpenRouter
                        # effort and max_tokens cannot be specified simultaneously
                        if self.provider == LLMProvider.OPENROUTER:
                            reasoning_max_tokens = os.environ.get("MOCO_REASONING_MAX_TOKENS", "1000")
                            extra_body["reasoning"] = {
                                "max_tokens": int(reasoning_max_tokens)
                            }
                        
                        create_kwargs = {
                            "model": self.model_name,
                            "messages": messages,
                            "tools": tools,
                            "stream": True,
                            "stream_options": {"include_usage": True},
                        }
                        # Enable parallel_tool_calls (OpenRouter models like kimi-k2.5 support this)
                        create_kwargs["parallel_tool_calls"] = True
                        
                        # Use reasoning_effort for OpenAI o1/o3
                        if self.provider != LLMProvider.OPENROUTER:
                            create_kwargs["reasoning_effort"] = "medium"
                        
                        if extra_body:
                            create_kwargs["extra_body"] = extra_body
                        
                        response = await self.openai_client.chat.completions.create(**create_kwargs)
                    else:
                        create_kwargs = {
                            "model": self.model_name,
                            "messages": messages,
                            "tools": tools,
                            "temperature": 0.7,
                            "stream": True,
                            "stream_options": {"include_usage": True},
                            "parallel_tool_calls": True,
                        }
                        response = await self.openai_client.chat.completions.create(**create_kwargs)

                    # Process streaming response
                    collected_content = ""
                    # Accumulate OpenAI tool call deltas by index (ai_manager style)
                    # idx -> {"id": str, "name": str, "arguments": str}
                    tool_calls_dict: Dict[int, Dict[str, str]] = {}
                    # Buffering thinking text (mitigation for fine chunks in GLM, etc.)
                    reasoning_buffer = ""  # For display purposes
                    collected_reasoning = ""  # Always capture for models like kimi-k2.5
                    reasoning_header_shown = False

                    async for chunk in response:
                        # Get usage information (included in the last chunk)
                        if hasattr(chunk, "usage") and chunk.usage:
                            self.last_usage = {
                                "prompt_tokens": int(chunk.usage.prompt_tokens or 0),
                                "completion_tokens": int(chunk.usage.completion_tokens or 0),
                                "total_tokens": int(chunk.usage.total_tokens or 0)
                            }

                        delta = chunk.choices[0].delta if chunk.choices else None
                        if not delta:
                            continue

                        # Processing reasoning/thinking content
                        # OpenRouter: delta.reasoning or delta.reasoning_details
                        # OpenAI o1/o3: delta.reasoning_content
                        reasoning_text = None
                        # OpenRouter: reasoning field (string)
                        if hasattr(delta, 'reasoning') and delta.reasoning:
                            reasoning_text = delta.reasoning
                        # OpenRouter: reasoning_details field (array)
                        elif hasattr(delta, 'reasoning_details') and delta.reasoning_details:
                            if isinstance(delta.reasoning_details, list):
                                for detail in delta.reasoning_details:
                                    # In case of dict
                                    if isinstance(detail, dict) and detail.get('text'):
                                        reasoning_text = detail['text']
                                        break
                                    # In case of object
                                    elif hasattr(detail, 'text') and detail.text:
                                        reasoning_text = detail.text
                                        break
                            else:
                                reasoning_text = str(delta.reasoning_details)
                        # OpenAI o1/o3: reasoning_content
                        elif hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                            reasoning_text = delta.reasoning_content
                        
                        if reasoning_text:
                            # Always capture reasoning for models like kimi-k2.5 that require it
                            collected_reasoning += reasoning_text
                            
                            if self.progress_callback:
                                # Via Web UI: Send batched via progress_callback
                                self.progress_callback(
                                    event_type="thinking",
                                    content=reasoning_text,
                                    agent_name=self.name
                                )
                            elif self.verbose:
                                # CLI direct execution: Show thinking process only in verbose mode
                                # Buffer and flush at periods, newlines, or a certain number of characters
                                reasoning_buffer += reasoning_text
                                # Show header only once
                                if not reasoning_header_shown:
                                    _safe_stream_print("\n💭 [Thinking...]\n")
                                    reasoning_header_shown = True
                                # Flush conditions: period, newline, or 80+ characters
                                while len(reasoning_buffer) >= 80 or any(c in reasoning_buffer for c in '.\n'):
                                    # If there is a period or newline, output until there
                                    flush_pos = -1
                                    for i, c in enumerate(reasoning_buffer):
                                        if c in '.\n':
                                            flush_pos = i + 1
                                            break
                                    if flush_pos == -1 and len(reasoning_buffer) >= 80:
                                        flush_pos = 80
                                    if flush_pos > 0:
                                        _safe_stream_print(reasoning_buffer[:flush_pos])
                                        reasoning_buffer = reasoning_buffer[flush_pos:]
                                    else:
                                        break
                        # Stream output text content
                        if delta.content:
                            if not self.progress_callback:
                                _safe_stream_print(delta.content)
                            collected_content += delta.content
                            self._partial_response = collected_content  # For recovery on error
                            if self.progress_callback:
                                self.progress_callback(
                                    event_type="chunk",
                                    content=delta.content,
                                    agent_name=self.name
                                )

                        # Collect tool call deltas (ai_manager style)
                        if delta.tool_calls:
                            for tc_delta in delta.tool_calls:
                                idx = getattr(tc_delta, "index", None)
                                if idx is None:
                                    idx = 0

                                if idx not in tool_calls_dict:
                                    tool_calls_dict[idx] = {"id": "", "name": "", "arguments": ""}

                                if getattr(tc_delta, "id", None):
                                    tool_calls_dict[idx]["id"] = tc_delta.id

                                if getattr(tc_delta, "function", None):
                                    if getattr(tc_delta.function, "name", None):
                                        tool_calls_dict[idx]["name"] += tc_delta.function.name
                                    if getattr(tc_delta.function, "arguments", None):
                                        tool_calls_dict[idx]["arguments"] += tc_delta.function.arguments

                    # Flush remaining thinking buffer (verbose only)
                    if reasoning_buffer and self.verbose and not self.progress_callback:
                        _safe_stream_print(reasoning_buffer)
                        reasoning_buffer = ""
                    if reasoning_header_shown and self.verbose and not self.progress_callback:
                        _safe_stream_print("\n[/Thinking]\n")

                    if collected_content and not self.progress_callback:
                        _safe_stream_print("\n")  # Newline

                    # Check for tool calls
                    # As tool_call_id might be missing in OpenAI streaming,
                    # determine based on the presence of function.name rather than the id
                    if tool_calls_dict and any(tc.get("name") for tc in tool_calls_dict.values()):
                        # Reconstruct tool_calls list (ai_manager style)
                        tool_calls_list = []
                        for idx in sorted(tool_calls_dict.keys()):
                            tc = tool_calls_dict[idx]
                            tc_id = tc.get("id") or f"call_{idx}"
                            tool_calls_list.append({
                                "id": tc_id,
                                "type": "function",
                                "function": {
                                    "name": tc.get("name", ""),
                                    "arguments": tc.get("arguments", ""),
                                }
                            })

                        assistant_msg = {
                            "role": "assistant",
                            "content": collected_content or "",
                            "tool_calls": tool_calls_list
                        }
                        # Include reasoning_content for models like kimi-k2.5 that require it
                        if collected_reasoning:
                            assistant_msg["reasoning_content"] = collected_reasoning
                        messages.append(assistant_msg)

                        for tc in tool_calls_list:
                            func_name = tc["function"]["name"]
                            raw_args = tc["function"].get("arguments") or ""

                            # If tool args are incomplete (e.g. "{" only), do not execute the tool.
                            # Return an error tool result so the model can retry properly.
                            stripped = raw_args.strip()
                            if not func_name:
                                result = "Error: tool call has empty function name"
                            elif not stripped:
                                result = "Error: tool call has empty arguments"
                            elif not (stripped.startswith("{") and stripped.endswith("}")):
                                result = "Error: tool call arguments are incomplete JSON"
                            else:
                                # Do not execute tools until arguments are fully parseable JSON.
                                # NOTE: Using default={} hides JSON parse failures and causes missing-required loops.
                                args_dict = SmartJSONParser.parse(raw_args, default=None)
                                if not isinstance(args_dict, dict):
                                    result = "Error: tool call arguments are invalid JSON (expected a single JSON object)"
                                else:
                                    result = await self._execute_tool_with_tracking(func_name, args_dict, session_id)

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": str(result)
                            })
                        
                        # Compress context when exceeding 80%
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
                        
                        continue  # Next iteration
                    else:
                        # If empty, return partial response
                        if not collected_content and self._partial_response:
                            return self._partial_response
                        return collected_content
                else:
                    # Non-streaming mode
                    # Use reasoning_effort for reasoning models (o1/o3)
                    if _is_reasoning_model(self.model_name):
                        create_kwargs = {
                            "model": self.model_name,
                            "messages": messages,
                            "tools": tools,
                        }
                        if self.provider != LLMProvider.OPENROUTER:
                            create_kwargs["reasoning_effort"] = "medium"
                        create_kwargs["parallel_tool_calls"] = True
                        response = await self.openai_client.chat.completions.create(**create_kwargs)
                    else:
                        create_kwargs = {
                            "model": self.model_name,
                            "messages": messages,
                            "tools": tools,
                            "temperature": 0.7,
                            "parallel_tool_calls": True,
                        }
                        response = await self.openai_client.chat.completions.create(**create_kwargs)
                    # usage recording
                    if hasattr(response, "usage") and response.usage:
                        self.last_usage = {
                            "prompt_tokens": int(response.usage.prompt_tokens or 0),
                            "completion_tokens": int(response.usage.completion_tokens or 0),
                            "total_tokens": int(response.usage.total_tokens or 0)
                        }
            except OperationCancelled:
                raise  # Re-raise to be handled by api.py
            except Exception as e:
                return f"Error calling OpenAI API: {e}"

            if not use_stream:
                choice = response.choices[0]
                message = choice.message
            else:
                continue  # Already processed above for streaming

            # Check for tool calls (non-streaming)
            if message.tool_calls:
                # Add assistant message
                assistant_msg = {
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
                }
                # Include reasoning_content for models like kimi-k2.5 that require it
                # Note: OpenRouter returns 'reasoning' but expects 'reasoning_content' in the request
                if hasattr(message, 'reasoning') and message.reasoning:
                    assistant_msg["reasoning_content"] = message.reasoning
                elif hasattr(message, 'reasoning_content') and message.reasoning_content:
                    assistant_msg["reasoning_content"] = message.reasoning_content
                messages.append(assistant_msg)

                # Tool execution (parallelized)
                async def execute_one(tc):
                    func_name = tc.function.name
                    # Do not execute tools until arguments are fully parseable JSON.
                    args_dict = SmartJSONParser.parse(tc.function.arguments, default=None)
                    if not isinstance(args_dict, dict):
                        result = "Error: tool call arguments are invalid JSON (expected a single JSON object)"
                    else:
                        result = await self._execute_tool_with_tracking(func_name, args_dict, session_id)
                    return {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(result)
                    }

                tasks = [execute_one(tc) for tc in message.tool_calls]
                tool_results = await asyncio.gather(*tasks)
                messages.extend(tool_results)
                
                # Compress context when exceeding 80%
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
                # Return text response
                content = message.content or ""
                # Emit progress events for non-streaming mode (e.g., ZAI with tools)
                if content and self.progress_callback:
                    self.progress_callback(event_type="start", agent_name=self.name)
                    self.progress_callback(event_type="chunk", content=content, agent_name=self.name)
                    self.progress_callback(event_type="done", agent_name=self.name)
                return content

        # If max_iterations is reached
        if self._partial_response:
            return f"[Max iterations reached]\n{self._partial_response}"
        return "Error: Max iterations reached without response."

    async def _run_gemini(self, user_input: str, history: Optional[List[Any]] = None, session_id: Optional[str] = None) -> str:
        """Execute agent with Gemini"""
        if history is None:
            history = []

        # System prompt construction
        system_instruction = self._get_system_prompt()

        # Tool settings
        tools_config = None
        if self.tool_declarations:
            # Passing Python functions directly often generates schemas automatically
            tools_config = [types.Tool(function_declarations=self.tool_declarations)]

        messages = []
        # Add history (convert dict format to Content format)
        for h in history:
            if isinstance(h, dict):
                role = h.get("role", "user")
                parts = h.get("parts", [])
                if not parts and "content" in h:
                    parts = [h["content"]]
                # Convert parts to Part objects
                part_objects = []
                for p in parts:
                    if isinstance(p, str):
                        part_objects.append(types.Part(text=p))
                    else:
                        part_objects.append(p)
                messages.append(types.Content(role=role, parts=part_objects))
            else:
                # If already in Content format
                messages.append(h)

        messages.append(types.Content(role="user", parts=[types.Part(text=user_input)]))

        # Settings - enable thinking mode for models that support it
        # Gemini 3 series and gemini-2.0-flash-thinking-exp support thinking
        model_lower = self.model_name.lower()
        supports_thinking = "gemini-3" in model_lower or "thinking" in model_lower
        
        if supports_thinking:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=tools_config,
                temperature=0.7,
                thinking_config=types.ThinkingConfig(
                    include_thoughts=True,
                    thinking_budget=-1,  # -1 = AUTO
                ),
            )
        else:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=tools_config,
                temperature=0.7,
            )

        while True:
            if session_id:
                check_cancelled(session_id)

            try:
                if self.stream:
                    # Streaming mode
                    response_stream = self.client.models.generate_content_stream(
                        model=self.model_name,
                        contents=messages,
                        config=config
                    )

                    collected_text = ""
                    collected_parts = []
                    function_calls = []

                    for chunk in response_stream:
                        # Get usage information
                        if chunk.usage_metadata:
                            self.last_usage = {
                                "prompt_tokens": int(chunk.usage_metadata.prompt_token_count or 0),
                                "completion_tokens": int(chunk.usage_metadata.candidates_token_count or 0),
                                "total_tokens": int(chunk.usage_metadata.total_token_count or 0)
                            }

                        if not chunk.candidates:
                            continue

                        candidate = chunk.candidates[0]
                        if not candidate.content:
                            continue

                        for part in candidate.content.parts or []:
                            # Display thinking process (verbose mode only)
                            if hasattr(part, 'thought') and part.thought and part.text:
                                if self.progress_callback:
                                    self.progress_callback(
                                        event_type="thinking",
                                        content=part.text,
                                        agent_name=self.name
                                    )
                                elif self.verbose:
                                    thought_text = f"\n💭 [Thinking...]\n{part.text}\n[/Thinking]\n"
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
                        # Tool execution (parallelized)
                        async def execute_one(fc):
                            func_name = fc.name
                            args = fc.args
                            args_dict = {}
                            if args:
                                if hasattr(args, "items"):
                                    args_dict = {k: v for k, v in args.items()}
                                elif isinstance(args, dict):
                                    args_dict = args
                            
                            result = await self._execute_tool_with_tracking(func_name, args_dict, session_id)
                            return types.Part(
                                function_response=types.FunctionResponse(
                                    name=func_name,
                                    response={"result": _ensure_jsonable(result)}
                                )
                            )

                        tasks = [execute_one(fc) for fc in function_calls]
                        tool_responses = await asyncio.gather(*tasks)
                        messages.append(types.Content(role="tool", parts=tool_responses))
                        continue
                    else:
                        return collected_text

                else:
                    # Non-streaming mode
                    response = self.client.models.generate_content(
                        model=self.model_name,
                        contents=messages,
                        config=config
                    )
                    
                    if response.usage_metadata:
                        self.last_usage = {
                            "prompt_tokens": int(response.usage_metadata.prompt_token_count or 0),
                            "completion_tokens": int(response.usage_metadata.candidates_token_count or 0),
                            "total_tokens": int(response.usage_metadata.total_token_count or 0)
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
                                print(f"\n💭 [Thinking...]\n{part.text}\n[/Thinking]")

                    function_calls = [p.function_call for p in message.parts if p.function_call]
                    if function_calls:
                        # Tool execution (parallelized)
                        async def execute_one(fc):
                            func_name = fc.name
                            args = fc.args
                            args_dict = {}
                            if args:
                                if hasattr(args, "items"):
                                    args_dict = {k: v for k, v in args.items()}
                                elif isinstance(args, dict):
                                    args_dict = args
                            
                            result = await self._execute_tool_with_tracking(func_name, args_dict, session_id)
                            return types.Part(
                                function_response=types.FunctionResponse(
                                    name=func_name,
                                    response={"result": _ensure_jsonable(result)}
                                )
                            )

                        tasks = [execute_one(fc) for fc in function_calls]
                        tool_responses = await asyncio.gather(*tasks)
                        messages.append(types.Content(role="tool", parts=tool_responses))
                        continue
                    else:
                        full_text = "".join(p.text for p in message.parts if p.text)
                        return full_text

            except OperationCancelled:
                raise  # Re-raise to be handled by api.py
            except Exception as e:
                import traceback
                traceback.print_exc()
                return f"Error calling Gemini API: {e}"
