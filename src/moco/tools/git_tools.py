import subprocess
import os
from typing import Optional, List, Dict, Any
from moco.utils.path import get_working_directory
from moco.core.llm_provider import get_available_provider, get_analyzer_model
from moco.core.runtime import AgentRuntime, LLMProvider

def execute_git(command: List[str]) -> str:
    """Gitコマンドを実行"""
    cwd = get_working_directory()
    try:
        result = subprocess.run(
            ["git"] + command,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30
        )
        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        return output.strip()
    except Exception as e:
        return f"Error executing git command: {e}"

def get_git_diff() -> str:
    """現在の差分を取得"""
    return execute_git(["diff", "HEAD"])

def get_git_status() -> str:
    """git status を取得"""
    return execute_git(["status"])

def get_git_history(limit: int = 5) -> str:
    """コミット履歴を取得"""
    return execute_git(["log", "-n", str(limit), "--oneline", "--graph", "--decorate"])

def generate_commit_message(diff: str) -> str:
    """差分からコミットメッセージを生成"""
    if not diff:
        return "chore: no changes detected"
    
    provider_name = get_available_provider()
    # Provider string to Enum conversion
    provider_map = {
        "gemini": LLMProvider.GEMINI,
        "openai": LLMProvider.OPENAI,
        "openrouter": LLMProvider.OPENROUTER,
        "zai": LLMProvider.ZAI
    }
    provider = provider_map.get(provider_name, LLMProvider.GEMINI)
    
    runtime = AgentRuntime(provider=provider)
    model = get_analyzer_model(provider_name)
    
    prompt = f"""以下のGit差分から、簡潔で適切なコミットメッセージを1行で生成してください。
接頭辞（feat:, fix:, chore:, docs: 等）を使用して、日本語で出力してください。
追加の説明は不要です。メッセージのみを返してください。

差分:
{diff[:4000]}
"""
    try:
        response = runtime.generate_completion(prompt, model=model)
        return response.strip().strip('"').strip("'")
    except Exception as e:
        return f"chore: automated commit ({e})"

def check_gh_cli() -> bool:
    """gh CLI が利用可能かチェック"""
    try:
        subprocess.run(["gh", "--version"], capture_output=True, check=True)
        return True
    except:
        return False

def create_pr() -> str:
    """GitHub CLIを使用してPR作成"""
    if not check_gh_cli():
        return "Error: GitHub CLI (gh) is not installed or not in PATH."
    
    cwd = get_working_directory()
    try:
        # まずはプッシュ
        push_result = subprocess.run(["git", "push"], cwd=cwd, capture_output=True, text=True)
        if push_result.returncode != 0:
            return f"Error: Git push failed.\n{push_result.stderr}"

        # PR作成 (Non-interactive with --fill)
        result = subprocess.run(
            ["gh", "pr", "create", "--fill"],
            capture_output=True,
            text=True,
            cwd=cwd
        )
        return result.stdout or result.stderr
    except Exception as e:
        return f"Error creating PR: {e}"
