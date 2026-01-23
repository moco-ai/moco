from typing import List, Dict, Any, Union, Optional
import ast
import json
import re
import sys
import os
from contextvars import ContextVar

# 動的インポート対応: 相対インポートが使えない場合は絶対パスでインポート
try:
    from ..storage.session_logger import SessionLogger
except ImportError:
    # moco のルートをパスに追加
    _moco_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _moco_root not in sys.path:
        sys.path.insert(0, _moco_root)
    from storage.session_logger import SessionLogger

# グローバルセッションID（Orchestratorが設定する）
_current_session_id_var: ContextVar[Optional[str]] = ContextVar("_current_session_id", default=None)

_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:json|python|txt)?\s*\n(?P<body>[\s\S]*?)\n```\s*$",
    re.IGNORECASE,
)
_TODOWRITE_WRAPPER_RE = re.compile(
    r"^\s*todowrite\s*\(\s*(?P<body>[\s\S]*?)\s*\)\s*;?\s*$",
    re.IGNORECASE,
)

_SMART_QUOTES = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "’": "'",
        "‘": "'",
        "‚": "'",
        "‛": "'",
    }
)


def _parse_todos_loose(value: str) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """LLMが生成した「JSONっぽい文字列」をできるだけ受け付ける。"""
    s = (value or "").strip()

    # 空文字列や null/None は空リストとして扱う
    if not s or s.lower() in ("null", "none", "undefined"):
        return []

    # コードフェンスを除去
    m = _CODE_FENCE_RE.match(s)
    if m:
        s = (m.group("body") or "").strip()

    # "todowrite(...)" のラッパを除去
    m = _TODOWRITE_WRAPPER_RE.match(s)
    if m:
        s = (m.group("body") or "").strip()

    # スマートクォートを通常のクォートへ
    s = s.translate(_SMART_QUOTES)

    # 末尾に説明文が混ざるケースがあるので、最初の配列/オブジェクトっぽい部分だけ抜き出す
    first_bracket = min([i for i in [s.find("["), s.find("{")] if i != -1], default=-1)
    if first_bracket >= 0:
        if first_bracket > 0:
            s = s[first_bracket:].strip()
        if "[" in s and "]" in s:
            s = s[: s.rfind("]") + 1].strip()
        elif "{" in s and "}" in s:
            s = s[: s.rfind("}") + 1].strip()

    # まずは厳密JSON
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        # フォールバック: Pythonリテラル
        s2 = re.sub(r"\btrue\b", "True", s, flags=re.IGNORECASE)
        s2 = re.sub(r"\bfalse\b", "False", s2, flags=re.IGNORECASE)
        s2 = re.sub(r"\bnull\b", "None", s2, flags=re.IGNORECASE)

        try:
            obj = ast.literal_eval(s2)
        except (ValueError, SyntaxError):
            # さらにフォールバック: クォートなしのキー/値を補正
            s3 = re.sub(r'(\{|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', s2)
            s3 = re.sub(r':\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(,|})', r':"\1"\2', s3)
            try:
                obj = json.loads(s3)
            except json.JSONDecodeError:
                try:
                    obj = ast.literal_eval(s3)
                except (ValueError, SyntaxError):
                    # 最終フォールバック: プレーンテキストを単一タスクとして扱う
                    # 例: "タスクの説明" → [{"id": "1", "content": "タスクの説明", "status": "pending"}]
                    clean_text = s.strip()
                    if clean_text and not clean_text.startswith(('[', '{')):
                        return [{"id": "1", "content": clean_text, "status": "pending"}]
                    raise Exception(f"Failed to parse todos: {s}")

    # {"todos": [...]} のラップを許容
    if isinstance(obj, dict) and "todos" in obj:
        obj = obj["todos"]

    return obj


def set_current_session(session_id: str) -> None:
    """現在のセッションIDを設定（Orchestratorから呼ばれる）"""
    _current_session_id_var.set(session_id)

def get_current_session() -> Optional[str]:
    """現在のセッションIDを取得"""
    return _current_session_id_var.get()

def todowrite(todos: Union[str, List[Dict[str, Any]]]) -> str:
    """
    Creates and manages a structured task list (todo list) for the current session.
    
    Args:
        todos: A JSON array of todo objects. Each object must have:
            - id: Unique identifier (string)
            - content: Task description (string)
            - status: One of "pending", "in_progress", "completed", "cancelled"
    
    Example:
        [
            {"id": "1", "content": "Research market data", "status": "pending"},
            {"id": "2", "content": "Write report", "status": "in_progress"}
        ]
    
    Returns:
        Success or error message.
    """
    session_id = get_current_session()

    if not session_id:
        return "Error: No active session. This tool must be called during an orchestration session."

    logger = SessionLogger()
    try:
        if isinstance(todos, str):
            todos = _parse_todos_loose(todos)

        if isinstance(todos, dict):
            todos = [todos]

        if not isinstance(todos, list):
            return "Error: Invalid JSON format for todos"
        if any(not isinstance(t, dict) for t in todos):
            return "Error: Invalid JSON format for todos"

        logger.save_todos(session_id, todos)
        return f"Todo list updated successfully. {len(todos)} items saved to session."
    except Exception as e:
        return f"Error updating todo list: {e}"

def todoread() -> str:
    """
    Reads the current todo list for the active session.
    If called by the orchestrator, it shows todos from all sub-agents as well.
    """
    global _current_session_id

    if not _current_session_id:
        return "Error: No active session. This tool must be called during an orchestration session."

    logger = SessionLogger()

    # Check if this is a sub-agent session
    is_sub_agent = False
    try:
        conn = logger._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT metadata FROM sessions WHERE session_id = ?", (_current_session_id,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            metadata = json.loads(row[0])
            if metadata.get("parent_session_id"):
                is_sub_agent = True
    except Exception:
        pass

    # If it's the orchestrator (no parent session), show all todos hierarchically
    if not is_sub_agent:
        return todoread_all()

    # Otherwise, show only the current agent's todos
    todos = logger.get_todos(_current_session_id)

    if not todos:
        return "No todos found for current session."

    status_icons = {
        "pending": "⬜",
        "in_progress": "🔄",
        "completed": "✅",
        "cancelled": "❌"
    }

    lines = ["=== Current Todo List ==="]
    for t in todos:
        icon = status_icons.get(t.get("status", "pending"), "?")
        lines.append(f"{icon} [{t.get('id', '?')}] {t.get('content', 'No content')}")

    return "\n".join(lines)

def todoread_all() -> str:
    """
    現在のセッションと全サブエージェントの todo リストを階層的に表示します。
    """
    global _current_session_id

    if not _current_session_id:
        return "Error: No active session."

    logger = SessionLogger()
    status_icons = {
        "pending": "⬜",
        "in_progress": "🔄",
        "completed": "✅",
        "cancelled": "❌"
    }

    all_lines = []
    main_todos = logger.get_todos(_current_session_id)
    all_lines.append("=== orchestrator ===")
    if main_todos:
        for t in main_todos:
            icon = status_icons.get(t.get("status", "pending"), "?")
            all_lines.append(f"{icon} [{t.get('id', '?')}] {t.get('content', 'No content')}")
    else:
        all_lines.append("(no todos)")

    try:
        conn = logger._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT session_id, title FROM sessions
            WHERE metadata LIKE ?
            ORDER BY created_at
        """, (f'%"parent_session_id": "{_current_session_id}"%',))
        sub_sessions = cursor.fetchall()
        conn.close()

        for sub_session_id, title in sub_sessions:
            agent_name = title.replace("Sub: @", "") if title.startswith("Sub: @") else title
            sub_todos = logger.get_todos(sub_session_id)
            all_lines.append(f"\n=== {agent_name} ===")
            if sub_todos:
                for t in sub_todos:
                    icon = status_icons.get(t.get("status", "pending"), "?")
                    all_lines.append(f"{icon} [{t.get('id', '?')}] {t.get('content', 'No content')}")
            else:
                all_lines.append("(no todos)")

    except Exception as e:
        all_lines.append(f"\n[Error loading sub-agent todos: {e}]")

    return "\n".join(all_lines)
