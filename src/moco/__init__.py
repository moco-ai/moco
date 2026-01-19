import warnings
# ========================================
# 警告の抑制 (インポート前に設定)
# ========================================
# Python 3.9 EOL や SSL 関連の不要な警告を非表示にする
warnings.filterwarnings("ignore", category=FutureWarning)
try:
    # urllib3 の NotOpenSSLWarning はインポート時に発生するため、先にフィルターを設定
    warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL 1.1.1+.*")
except Exception:
    pass

# ========================================
# 重要: .env の読み込みは最初に行う必要がある
# 他のモジュールがインポート時に環境変数を参照するため
# ========================================
import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

def _early_load_dotenv():
    """モジュールインポート前に .env を読み込む"""
    env_path = find_dotenv(usecwd=True) or (Path(__file__).parent.parent.parent / ".env")
    if env_path:
        load_dotenv(env_path)

_early_load_dotenv()

# ここから通常のインポート
from .core.orchestrator import Orchestrator
from .tools.discovery import AgentLoader, AgentConfig
from .core.runtime import AgentRuntime, LLMProvider
from .storage.session_logger import SessionLogger
from .storage.semantic_memory import SemanticMemory
from .tools import TOOL_MAP

__all__ = [
    "Orchestrator",
    "AgentLoader",
    "AgentConfig",
    "AgentRuntime",
    "LLMProvider",
    "SessionLogger",
    "SemanticMemory",
    "TOOL_MAP"
]
