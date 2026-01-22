# ruff: noqa: E402
import warnings
# ========================================
# Suppress warnings (Set before imports)
# ========================================
# Hide unnecessary warnings related to Python 3.9 EOL, SSL, etc.
warnings.filterwarnings("ignore", category=FutureWarning)
try:
    # urllib3's NotOpenSSLWarning occurs during import, so set the filter first
    warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL 1.1.1+.*")
except Exception:
    pass

# ========================================
# IMPORTANT: Loading .env must be the first thing to do
# Other modules reference environment variables during import
# ========================================
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

def _early_load_dotenv():
    """Load .env before module imports"""
    env_path = find_dotenv(usecwd=True) or (Path(__file__).parent.parent.parent / ".env")
    if env_path:
        load_dotenv(env_path)

_early_load_dotenv()

# Normal imports from here
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
