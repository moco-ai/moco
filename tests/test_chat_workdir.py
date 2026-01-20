"""Test chat working directory option"""
import pytest
from pathlib import Path
from unittest.mock import Mock, patch
import tempfile
import os


@pytest.fixture
def test_dir():
    """Create a temporary test directory"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def test_chat_with_working_dir(test_dir):
    """Test that --working-dir option is passed to Orchestrator"""
    from moco.cli import chat

    # Mock the orchestrator to prevent actual initialization
    with patch('moco.cli.Orchestrator') as mock_orchestrator_class:
        mock_o = Mock()
        mock_o.session_logger.list_sessions.return_value = []
        mock_o.create_session.return_value = 'test-session-id'
        mock_o.run_sync.return_value = 'test response'
        mock_orchestrator_class.return_value = mock_o

        # Mock console.input to simulate exit
        with patch('moco.cli.Console') as mock_console_class:
            mock_console = Mock()
            mock_console.input.side_effect = ['exit']
            mock_console_class.return_value = mock_console

            # Mock profile selection
            with patch('moco.cli.prompt_profile_selection', return_value='default'):
                with patch('moco.cli.get_available_provider', return_value='openai'):
                    try:
                        chat(
                            profile='default',
                            provider='openai',
                            model='gpt-4',
                            working_dir=str(test_dir)
                        )
                    except SystemExit:
                        pass  # Expected when exiting

    # Verify Orchestrator was called with working_directory
    call_kwargs = mock_orchestrator_class.call_args[1]
    assert 'working_directory' in call_kwargs
    assert call_kwargs['working_directory'] == str(test_dir.resolve())


def test_chat_with_invalid_working_dir():
    """Test that invalid working directory is handled"""
    from moco.cli import chat
    from typer import Exit

    # Mock the orchestrator to prevent actual initialization
    with patch('moco.cli.Orchestrator') as mock_orchestrator_class:
        mock_o = Mock()
        mock_o.session_logger.list_sessions.return_value = []
        mock_o.create_session.return_value = 'test-session-id'
        mock_o.run_sync.return_value = 'test response'
        mock_orchestrator_class.return_value = mock_o

        # Mock console
        with patch('moco.cli.Console') as mock_console_class:
            mock_console = Mock()
            mock_console_class.return_value = mock_console

            # Mock profile selection
            with patch('moco.cli.prompt_profile_selection', return_value='default'):
                with patch('moco.cli.get_available_provider', return_value='openai'):
                    with pytest.raises(Exit) as exc_info:
                        chat(
                            profile='default',
                            provider='openai',
                            model='gpt-4',
                            working_dir='/nonexistent/directory/12345'
                        )

                    assert exc_info.value.exit_code == 1
