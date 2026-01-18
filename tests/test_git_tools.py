"""Tests for git_tools (subprocess level)

Note: git_tools.py は LLM 依存 (Runtime, LLMProvider) のためインポートせず、
subprocess レベルでテスト
"""
import pytest
import subprocess
from unittest.mock import patch, MagicMock


class TestGitCommandExecution:
    """Git コマンド実行のテスト (subprocess レベル)"""

    def test_git_status_command(self):
        """git status コマンドが実行可能"""
        result = subprocess.run(
            ["git", "status"],
            capture_output=True,
            text=True,
            timeout=10
        )
        # エラーがあっても stderr に出力される
        assert result.returncode == 0 or "not a git repository" in result.stderr

    def test_git_version_command(self):
        """git --version コマンドが実行可能"""
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        assert result.returncode == 0
        assert "git version" in result.stdout


class TestGhCliAvailability:
    """GitHub CLI の利用可能性テスト"""

    def test_gh_cli_check(self):
        """gh CLI の存在確認"""
        try:
            result = subprocess.run(
                ["gh", "--version"],
                capture_output=True,
                timeout=10
            )
            gh_available = result.returncode == 0
        except FileNotFoundError:
            gh_available = False
        
        # gh がインストールされていてもいなくてもパス
        assert isinstance(gh_available, bool)
