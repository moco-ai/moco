"""
LLM プロバイダー統一管理

プロバイダー優先順位: 1. zai, 2. openrouter, 3. gemini
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
from typing import Optional, Tuple

logger = logging.getLogger(__name__)
_DOTENV_LOADED = False


def _ensure_dotenv_loaded() -> None:
    """必要に応じて .env を読み込む（多重読み込みを避ける）。"""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    env_path = find_dotenv(usecwd=True) or (Path(__file__).parent.parent.parent / ".env")
    if env_path:
        load_dotenv(env_path)
    _DOTENV_LOADED = True

# プロバイダー定数
PROVIDER_ZAI = "zai"
PROVIDER_OPENROUTER = "openrouter"
PROVIDER_GEMINI = "gemini"
PROVIDER_OPENAI = "openai"

# プロバイダー優先順位
PROVIDER_PRIORITY = [PROVIDER_ZAI, PROVIDER_OPENROUTER, PROVIDER_GEMINI]

# プロバイダーごとのデフォルトモデル
DEFAULT_MODELS = {
    PROVIDER_ZAI: "glm-4.7",
    PROVIDER_OPENROUTER: "moonshotai/kimi-k2.5",
    PROVIDER_GEMINI: "gemini-2.0-flash",
    PROVIDER_OPENAI: "gpt-4o",
}

# 分析用（軽量）モデル
ANALYZER_MODELS = {
    PROVIDER_ZAI: "glm-4.7-flash",
    PROVIDER_OPENROUTER: "google/gemini-3-flash-preview",
    PROVIDER_GEMINI: "gemini-2.0-flash",
    PROVIDER_OPENAI: "gpt-4o-mini",
}


def _check_api_key(provider: str) -> bool:
    """指定プロバイダーの API キーが設定されているか確認"""
    _ensure_dotenv_loaded()
    if provider == PROVIDER_ZAI:
        return bool(os.environ.get("ZAI_API_KEY"))
    elif provider == PROVIDER_OPENROUTER:
        return bool(os.environ.get("OPENROUTER_API_KEY"))
    elif provider == PROVIDER_GEMINI:
        return bool(os.environ.get("GENAI_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    elif provider == PROVIDER_OPENAI:
        return bool(os.environ.get("OPENAI_API_KEY"))
    return False


def get_available_provider() -> str:
    """
    利用可能なプロバイダーを優先順位で返す。
    
    優先順位: zai → openrouter → gemini
    
    環境変数 MOCO_DEFAULT_PROVIDER で強制指定可能。
    
    Returns:
        利用可能なプロバイダー名
    """
    # 環境変数で強制指定
    forced = os.environ.get("MOCO_DEFAULT_PROVIDER")
    if forced and forced in [PROVIDER_ZAI, PROVIDER_OPENROUTER, PROVIDER_GEMINI, PROVIDER_OPENAI]:
        if _check_api_key(forced):
            logger.info(f"Using forced provider: {forced}")
            return forced
        else:
            logger.warning(f"Forced provider {forced} has no API key, falling back to priority order")
    
    # 優先順位で確認
    for provider in PROVIDER_PRIORITY:
        if _check_api_key(provider):
            logger.debug(f"Selected provider by priority: {provider}")
            return provider
    
    # どれも利用できない場合は openrouter をデフォルトに（エラーは後で発生）
    logger.warning("No API keys found, defaulting to openrouter")
    return PROVIDER_OPENROUTER


def get_default_model(provider: Optional[str] = None) -> str:
    """
    プロバイダーのデフォルトモデルを返す。
    
    Args:
        provider: プロバイダー名（省略時は自動選択）
    
    Returns:
        モデル名
    """
    if provider is None:
        provider = get_available_provider()
    return DEFAULT_MODELS.get(provider, DEFAULT_MODELS[PROVIDER_OPENROUTER])


def get_analyzer_model(provider: Optional[str] = None) -> str:
    """
    分析用の軽量モデルを返す。
    
    環境変数 MOCO_ANALYZER_MODEL で上書き可能。
    
    Args:
        provider: プロバイダー名（省略時は自動選択）
    
    Returns:
        モデル名
    """
    # 環境変数で上書き
    override = os.environ.get("MOCO_ANALYZER_MODEL")
    if override:
        return override
    
    if provider is None:
        provider = get_available_provider()
    return ANALYZER_MODELS.get(provider, ANALYZER_MODELS[PROVIDER_OPENROUTER])


def get_provider_and_model() -> Tuple[str, str]:
    """
    利用可能なプロバイダーとそのデフォルトモデルを返す。
    
    Returns:
        (provider, model) のタプル
    """
    provider = get_available_provider()
    model = get_default_model(provider)
    return provider, model


def resolve_provider_and_model(provider_str: Optional[str], model_str: Optional[str]) -> Tuple[str, Optional[str]]:
    """
    "zai/glm-4.7" のような形式をパースし、provider と model を返す。
    
    Args:
        provider_str: プロバイダー文字列（例: "zai/glm-4.7", "openai"）
        model_str: 明示的に指定されたモデル名
        
    Returns:
        (provider_name, model_name) のタプル
    """
    if provider_str is None:
        provider_str = get_available_provider()

    provider_name = provider_str
    model_name = model_str
    
    if "/" in provider_str and model_name is None:
        parts = provider_str.split("/", 1)
        provider_name = parts[0]
        model_name = parts[1]
        
    return provider_name, model_name
