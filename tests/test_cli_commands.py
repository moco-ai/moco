"""Test CLI commands"""
import pytest
from pathlib import Path
from moco.cli_commands import handle_workdir, handle_help, handle_theme, handle_model
from moco.ui.theme import ThemeName
from unittest.mock import Mock, patch


@pytest.fixture
def console():
    from rich.console import Console
    return Console()


@pytest.fixture
def mock_orchestrator():
    orchestrator = Mock()
    orchestrator.working_directory = "/tmp"
    orchestrator.model = "claude-3-5-sonnet-20241022"
    orchestrator.profile = "default"
    orchestrator.runtimes = {
        "agent1": Mock(working_directory="/tmp"),
        "agent2": Mock(working_directory="/tmp"),
    }
    return orchestrator


@pytest.fixture
def context(console, mock_orchestrator):
    return {
        'console': console,
        'orchestrator': mock_orchestrator,
        'session_id': 'test-session-123',
    }


def test_handle_workdir_show(context, mock_orchestrator):
    """Show current working directory"""
    result = handle_workdir([], context)
    assert result is True
    assert mock_orchestrator.working_directory == "/tmp"


def test_handle_workdir_change(context, mock_orchestrator):
    """Change working directory"""
    test_dir = Path("/tmp")
    result = handle_workdir([str(test_dir)], context)
    assert result is True
    assert mock_orchestrator.working_directory == str(test_dir.resolve())

    # Check runtime working directories are updated
    for runtime in mock_orchestrator.runtimes.values():
        assert runtime.working_directory == str(test_dir.resolve())


def test_handle_workdir_invalid(context):
    """Handle invalid directory"""
    result = handle_workdir(["/nonexistent/directory"], context)
    assert result is True  # Should not exit, just show error


def test_handle_help(context):
    """Show help"""
    result = handle_help([], context)
    assert result is True


def test_handle_theme_show(context):
    """Show current theme"""
    result = handle_theme([], context)
    assert result is True


def test_handle_theme_change(context):
    """Change theme"""
    result = handle_theme(["light"], context)
    assert result is True


def test_handle_model_show(context, mock_orchestrator):
    """Show current model"""
    result = handle_model([], context)
    assert result is True


def test_handle_model_change(context, mock_orchestrator):
    """Change model"""
    result = handle_model(["gpt-4"], context)
    assert result is True
    assert mock_orchestrator.model == "gpt-4"
