import logging

from .base import read_file, write_file, edit_file, execute_bash
from .todo import todowrite, todoread
from .web import websearch, webfetch
from .filesystem import list_dir, glob_search, tree, file_info
from .search import grep, find_definition, find_references, ripgrep
from .file_upload import file_upload, file_upload_str
from .vision import analyze_image
from .image_gen import generate_image
from .wait import wait
from .process import (
    start_background,
    stop_process,
    list_processes,
    get_output,
    wait_for_pattern,
    wait_for_exit,
    send_input,
)
from .skill_loader import SkillLoader, SkillConfig
from .skill_tools import load_skill, list_loaded_skills, execute_skill
from .project_context import get_project_context
from .mobile import NotifyMobileTool, RequestLocationTool, send_file_to_mobile, get_pending_artifacts, clear_artifacts, set_current_session
from .scheduler import schedule_task, list_scheduled_tasks, remove_scheduled_task

logger = logging.getLogger(__name__)

# codebase_search は埋め込みAPIキーが必要なため、利用不可時は graceful に無効化
try:
    from .codebase_search import codebase_search
    CODEBASE_SEARCH_AVAILABLE = True
except (ImportError, ValueError) as e:
    logger.warning(f"codebase_search is disabled: {e}")
    CODEBASE_SEARCH_AVAILABLE = False

    def codebase_search(query: str, target_dir: str = ".", top_k: int = 5) -> str:
        """codebase_search is unavailable (no embedding API key configured)."""
        return (
            "Error: codebase_search is unavailable. "
            "Please set OPENAI_API_KEY or GEMINI_API_KEY environment variable."
        )

# semantic_search も埋め込みAPIキーが必要
try:
    from .semantic_search import semantic_search
    SEMANTIC_SEARCH_AVAILABLE = True
except (ImportError, ValueError) as e:
    logger.warning(f"semantic_search is disabled: {e}")
    SEMANTIC_SEARCH_AVAILABLE = False

    def semantic_search(query: str, target_dir: str = ".", top_k: int = 5) -> str:
        """semantic_search is unavailable (no embedding API key configured)."""
        return (
            "Error: semantic_search is unavailable. "
            "Please set OPENAI_API_KEY or GEMINI_API_KEY environment variable."
        )

TOOL_MAP = {
    # ファイル操作
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "execute_bash": execute_bash,
    # 互換性のためのエイリアス
    "read": read_file,
    "write": write_file,
    "edit": edit_file,
    "bash": execute_bash,
    # TODO管理
    "todowrite": todowrite,
    "todoread": todoread,
    # Web検索
    "websearch": websearch,
    "webfetch": webfetch,
    # ファイルシステム
    "list_dir": list_dir,
    "glob_search": glob_search,
    "tree": tree,
    "file_info": file_info,
    # 検索
    "grep": grep,
    "find_definition": find_definition,
    "find_references": find_references,
    "ripgrep": ripgrep,
    "codebase_search": codebase_search,
    "semantic_search": semantic_search,
    # ファイルアップロード
    "file_upload": file_upload_str,
    # 画像解析
    "analyze_image": analyze_image,
    # 画像生成
    "generate_image": generate_image,
    # 待機
    "wait": wait,
    # バックグラウンドプロセス管理
    "start_background": start_background,
    "stop_process": stop_process,
    "list_processes": list_processes,
    "get_output": get_output,
    "wait_for_pattern": wait_for_pattern,
    "wait_for_exit": wait_for_exit,
    "send_input": send_input,
    # Skills 管理
    "load_skill": load_skill,
    "list_loaded_skills": list_loaded_skills,
    "execute_skill": execute_skill,
    # プロジェクトコンテキスト
    "get_project_context": get_project_context,
    # モバイル通知
    "notify_mobile": NotifyMobileTool().run,
    "request_location": RequestLocationTool().run,
    "send_file_to_mobile": send_file_to_mobile,
    # スケジューラ
    "schedule_task": schedule_task,
    "list_scheduled_tasks": list_scheduled_tasks,
    "remove_scheduled_task": remove_scheduled_task,
    # NOTE: browser_* ツールは discovery.py で自動的に読み込まれる
}

# Re-exported symbols (public API)
__all__ = [
    "TOOL_MAP",
    "file_upload",
    "file_upload_str",
    "SkillLoader",
    "SkillConfig",
]
