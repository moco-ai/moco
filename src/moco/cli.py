#!/usr/bin/env python3
"""Moco CLI"""

import warnings
# ========================================
# Suppress warnings (Set before imports)
# ========================================
# Hide unnecessary warnings related to Python 3.9 EOL, SSL, etc.
warnings.filterwarnings("ignore", category=FutureWarning)
try:
    # urllib3's NotOpenSSLWarning occurs during import, so
    # the warning filter must be set first.
    warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL 1.1.1+.*")
except Exception:
    pass

# ========================================
# IMPORTANT: Loading .env must be the first thing to do
# Other modules reference environment variables during import
# ========================================
import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

def _early_load_dotenv():
    """Load .env before module imports"""
    env_path = find_dotenv(usecwd=True) or (Path(__file__).parent.parent.parent / ".env")
    if env_path:
        load_dotenv(env_path)

# Load environment variables before importing other modules
_early_load_dotenv()

# Normal imports from here
import typer
import time
import sys
import threading
from datetime import datetime
from typing import Optional, List
from .ui.theme import ThemeName, THEMES

def init_environment():
    """Environment variable initialization (kept for backward compatibility)"""
    # Already loaded in _early_load_dotenv(), but
    # reload if explicitly called.
    env_path = find_dotenv(usecwd=True) or (Path(__file__).parent.parent.parent / ".env")
    if env_path:
        load_dotenv(env_path, override=True)


def resolve_provider(provider_str: str, model: Optional[str] = None) -> tuple:
    """Resolve provider string and return LLMProvider and model name.
    
    Args:
        provider_str: Provider string (e.g., "gemini", "zai/glm-4.7")
        model: Model name (if already specified)
    
    Returns:
        tuple: (LLMProvider, model_name) - raises typer.Exit for invalid providers
    """
    from .core.runtime import LLMProvider
    
    # Parse format like "zai/glm-4.7"
    provider_name = provider_str
    resolved_model = model
    if "/" in provider_str and model is None:
        parts = provider_str.split("/", 1)
        provider_name = parts[0]
        resolved_model = parts[1]
    
    # Provider name validation and mapping
    VALID_PROVIDERS = {
        "openai": LLMProvider.OPENAI,
        "openrouter": LLMProvider.OPENROUTER,
        "zai": LLMProvider.ZAI,
        "gemini": LLMProvider.GEMINI,
    }
    
    if provider_name not in VALID_PROVIDERS:
        valid_list = ", ".join(sorted(VALID_PROVIDERS.keys()))
        typer.echo(f"Error: Unknown provider '{provider_name}'. Valid options: {valid_list}", err=True)
        raise typer.Exit(code=1)
    
    return VALID_PROVIDERS[provider_name], resolved_model


app = typer.Typer(
    name="Moco",
    help="Lightweight AI agent orchestration framework",
    add_completion=False,
)

# Subcommands for session management
sessions_app = typer.Typer(help="Session management")
app.add_typer(sessions_app, name="sessions")

# Subcommands for skills management
skills_app = typer.Typer(help="Skills management (Claude Skills compatible)")
app.add_typer(skills_app, name="skills")

# Subcommands for task management
tasks_app = typer.Typer(help="Task management")
app.add_typer(tasks_app, name="tasks")


def get_available_profiles() -> List[str]:
    """Get list of available profiles"""
    profiles = []
    
    # 1. profiles/ in current directory
    cwd_profiles = Path.cwd() / "profiles"
    if cwd_profiles.exists():
        for p in cwd_profiles.iterdir():
            if p.is_dir() and (p / "profile.yaml").exists():
                profiles.append(p.name)
    
    # 2. Built-in profiles in package
    pkg_profiles = Path(__file__).parent / "profiles"
    if pkg_profiles.exists():
        for p in pkg_profiles.iterdir():
            if p.is_dir() and (p / "profile.yaml").exists():
                if p.name not in profiles:
                    profiles.append(p.name)
    
    return sorted(profiles) if profiles else ["default"]


def complete_profile(incomplete: str) -> List[str]:
    """Tab completion for profile names"""
    profiles = get_available_profiles()
    return [p for p in profiles if p.startswith(incomplete)]


def prompt_profile_selection() -> str:
    """Interactively select a profile"""
    from rich.console import Console
    from rich.prompt import Prompt
    
    console = Console()
    profiles = get_available_profiles()
    
    if len(profiles) == 1:
        return profiles[0]
    
    console.print("\n[bold]Available profiles:[/]")
    for i, p in enumerate(profiles, 1):
        console.print(f"  [cyan]{i}[/]. {p}")
    
    choice = Prompt.ask(
        "\n[bold]Select profile[/]",
        choices=[str(i) for i in range(1, len(profiles) + 1)] + profiles,
        default="1"
    )
    
    # Selected by number
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(profiles):
            return profiles[idx]
    
    # Selected by name
    if choice in profiles:
        return choice
    
    return profiles[0]


@app.command()
def run(
    task: str = typer.Argument(..., help="Task to execute"),
    profile: str = typer.Option("default", "--profile", "-p", help="Profile to use", autocompletion=complete_profile),
    provider: Optional[str] = typer.Option(None, "--provider", "-P", help="LLM Provider (gemini/openai/openrouter/zai) - auto-select if omitted"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model name to use (e.g., gpt-4o, gemini-2.5-pro, claude-sonnet-4)"),
    stream: bool = typer.Option(False, "--stream/--no-stream", help="Enable streaming output (default: off)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
    rich_output: bool = typer.Option(True, "--rich/--plain", help="Rich output"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="Session name (continue or new)"),
    cont: bool = typer.Option(False, "--continue", "-c", help="Continue the last session"),
    auto_retry: int = typer.Option(0, "--auto-retry", help="Automatic retry count on error"),
    retry_delay: int = typer.Option(3, "--retry-delay", help="Retry delay (seconds)"),
    show_metrics: bool = typer.Option(False, "--show-metrics", "-M", help="Display metrics"),
    theme: ThemeName = typer.Option(ThemeName.DEFAULT, "--theme", help="UI color theme", case_sensitive=False),
    use_optimizer: bool = typer.Option(False, "--optimizer/--no-optimizer", help="Automatic agent selection by Optimizer"),
    working_dir: Optional[str] = typer.Option(None, "--working-dir", "-w", help="Working directory (auto-propagated to subagents)"),
):
    """Execute a task"""
    if session and cont:
        typer.echo("Error: --session and --continue cannot be specified at the same time.", err=True)
        raise typer.Exit(code=1)

    from .ui.layout import ui_state
    ui_state.theme = theme

    theme_config = THEMES[theme]

    init_environment()

    # Validation and setting of working directory
    if working_dir:
        path = Path(working_dir).resolve()
        if not path.is_dir():
            typer.echo(f"Error: Directory does not exist: {working_dir}", err=True)
            raise typer.Exit(code=1)
        os.environ['MOCO_WORKING_DIRECTORY'] = str(path)

    from .core.orchestrator import Orchestrator
    from .core.llm_provider import get_available_provider

    # Resolve provider (auto-select if not specified)
    if provider is None:
        provider = get_available_provider()

    provider_enum, model = resolve_provider(provider, model)

    if rich_output:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()

    o = Orchestrator(
        profile=profile,
        provider=provider_enum,
        model=model,
        stream=stream,
        verbose=verbose,
        use_optimizer=use_optimizer,
        working_directory=working_dir,
    )

    # Session management
    session_id = None
    if cont:
        # Get the last session
        sessions = o.session_logger.list_sessions(limit=1)
        if sessions:
            session_id = sessions[0].get("session_id")
            if rich_output:
                console.print(f"[dim]Continuing session: {session_id[:8]}...[/dim]")
        else:
            typer.echo("Warning: No session to continue. Creating a new one.", err=True)
    elif session:
        # Search or create named session
        sessions = o.session_logger.list_sessions(limit=50)
        for s in sessions:
            if s.get("title", "").endswith(f"[{session}]"):
                session_id = s.get("session_id")
                if rich_output:
                    console.print(f"[dim]Resuming session: {session}[/dim]")
                break

    if not session_id:
        title = f"CLI: {task[:40]}" + (f" [{session}]" if session else "")
        session_id = o.create_session(title=title)

    if rich_output:
        header = f"[bold {theme_config.status}]Profile:[/] {profile}  [bold {theme_config.status}]Provider:[/] {provider}"
        if session:
            header += f"  [bold {theme_config.status}]Session:[/] {session}"
        console.print(Panel(header, title="🤖 Moco", border_style=theme_config.tools))
        console.print()

    # Execute (Retry support)
    start_time = time.time()
    result = None
    last_error = None

    from .cancellation import create_cancel_event, request_cancel, clear_cancel_event, OperationCancelled
    create_cancel_event(session_id)

    try:
        for attempt in range(auto_retry + 1):
            try:
                result = o.run_sync(task, session_id)
                break
            except (KeyboardInterrupt, OperationCancelled):
                request_cancel(session_id)
                if rich_output:
                    console.print(f"\n[bold red]Cancelled[/bold red] (Session: {session_id[:8]}...)")
                else:
                    print(f"\nCancelled (Session: {session_id[:8]}...)")
                raise typer.Exit(code=0)
            except Exception as e:
                last_error = e
                if attempt < auto_retry:
                    if rich_output:
                        console.print(f"[yellow]Error: {e}. Retrying in {retry_delay}s... ({attempt + 1}/{auto_retry})[/yellow]")
                    time.sleep(retry_delay)
                else:
                    if rich_output:
                        console.print(f"[red]Error: {e}[/red]")
                        _print_error_hints(console, e)
                    raise typer.Exit(code=1)
    finally:
        clear_cancel_event(session_id)

    elapsed = time.time() - start_time

    if rich_output and result:
        console.print()
        _print_result(console, result, theme_name=theme, verbose=verbose)

        if show_metrics:
            console.print()
            console.print(Panel(
                f"[bold]Elapsed:[/] {elapsed:.1f}s\n"
                f"[bold]Session:[/] {session_id[:8]}...",
                title="📊 Metrics",
                border_style=theme_config.status,
            ))
    elif result:
        print("\n--- Result ---")
        print(result)




def _print_error_hints(console, error: Exception):
    """Display hints based on error type"""
    from rich.panel import Panel

    error_str = str(error).lower()
    hints = []

    if "rate limit" in error_str or "429" in error_str:
        hints.append("• Rate limit reached. Please wait a moment before retrying.")
        hints.append("• Try changing the --provider.")
    elif "api key" in error_str or "authentication" in error_str:
        hints.append("• Check your API keys.")
        hints.append("• Verify if correct keys are set in the .env file.")
    elif "context" in error_str or "token" in error_str:
        hints.append("• The prompt might be too long.")
        hints.append("• Try splitting the task into smaller parts.")
    else:
        hints.append("• Check verbose logs with the --verbose option.")
        hints.append("• Try retrying with --auto-retry.")

    console.print(Panel("\n".join(hints), title="💡 Hints", border_style="yellow"))


def _print_result(console, result: str, theme_name: ThemeName = ThemeName.DEFAULT, verbose: bool = False):
    """Format and display result (Simple text output)

    Args:
        console: Rich console
        result: Result string
        verbose: If True, display all agent outputs; if False, only the last one
    """
    import re

    theme = THEMES[theme_name]

    # Extract final summary
    final_summary = ""
    # Japanese markers kept if they are structure, but translation requested
    if "\n---\n## まとめ" in result:
        parts = result.split("\n---\n## まとめ")
        result = parts[0]
        final_summary = parts[1].strip() if len(parts) > 1 else ""
    elif "\n---\n✅" in result:
        parts = result.split("\n---\n✅")
        result = parts[0]
        final_summary = parts[1].strip() if len(parts) > 1 else ""

    # Split by @agent: Response pattern
    sections = re.split(r'(@[\w-]+):\s*', result)

    if len(sections) > 1:
        if verbose:
            # Display all agent outputs
            i = 1
            while i < len(sections):
                agent = sections[i]
                content = sections[i + 1].strip() if i + 1 < len(sections) else ""
                if content:
                    # Truncate if too long
                    lines = content.split('\n')
                    if len(lines) > 30:
                        content = '\n'.join(lines[:30]) + f"\n... ({len(lines) - 30} lines omitted)"
                    console.print(f"\n[bold {theme.thoughts}]{agent}[/]")
                    console.print(content)
                i += 2
        else:
            # Display only the last agent result
            last_agent = sections[-2] if len(sections) >= 2 else ""
            last_content = sections[-1].strip() if sections[-1] else ""

            # Do not truncate @orchestrator's final response, shorten others
            if last_agent == "@orchestrator":
                display = last_content
            else:
                lines = last_content.split('\n')
                if len(lines) > 20:
                    display = '\n'.join(lines[:20]) + f"\n\n[dim]... ({len(lines) - 20} lines omitted, use -v for full output)[/dim]"
                else:
                    display = last_content

            console.print(f"\n[bold {theme.thoughts}]{last_agent}[/]")
            console.print(display)

    # Display final summary
    if final_summary:
        console.print(f"\n[bold {theme.result}]✅ Summary[/]")
        console.print(final_summary)
    elif len(sections) > 1:
        console.print(f"\n[bold {theme.result}]✅ Done[/]")
    else:
        # Single response
        console.print(result)


@sessions_app.command("list")
def sessions_list(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of sessions to display"),
):
    """List session history"""
    from rich.console import Console
    from rich.table import Table
    from .storage.session_logger import SessionLogger
    from .ui.layout import ui_state

    console = Console()
    theme = THEMES.get(ui_state.theme, THEMES[ThemeName.DEFAULT])
    logger = SessionLogger()
    sessions = logger.list_sessions(limit=limit)

    if not sessions:
        console.print("[dim]No sessions found.[/dim]")
        return

    table = Table(title="Sessions", border_style=theme.tools)
    table.add_column("ID", style=theme.tools, width=13)
    table.add_column("Title", style=theme.result)
    table.add_column("Profile", style=theme.status)
    table.add_column("Created", style="dim")

    for s in sessions:
        table.add_row(
            s.get("session_id", "")[:8] + "...",
            s.get("title", "")[:40],
            s.get("profile", ""),
            s.get("created_at", "")[:19],
        )

    console.print(table)


@sessions_app.command("show")
def sessions_show(
    session_id: str = typer.Argument(..., help="Session ID (Partial ID allowed)"),
):
    """Display session history"""
    from rich.console import Console
    from rich.panel import Panel
    from .storage.session_logger import SessionLogger
    from .ui.layout import ui_state

    theme = THEMES[ui_state.theme]
    console = Console()
    logger = SessionLogger()

    # Search session by partial match
    sessions = logger.list_sessions(limit=100)
    found_id = None
    for s in sessions:
        if s.get("session_id", "").startswith(session_id):
            found_id = s.get("session_id")
            break

    if not found_id:
        console.print(f"[red]Session not found: {session_id}[/red]")
        raise typer.Exit(code=1)

    history = logger.get_agent_history(found_id, limit=50)

    console.print(Panel(f"Session: {found_id}", border_style=theme.tools))

    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            console.print(f"[bold {theme.status}]User:[/] {content[:200]}...")
        else:
            console.print(f"[bold {theme.result}]Assistant:[/] {content[:200]}...")
        console.print()


@app.command("list-profiles")
def list_profiles():
    """List available profiles"""
    profiles_dir = Path.cwd() / "moco" / "profiles"
    if not profiles_dir.exists():
        # Fallback to absolute path from project root if possible, or current dir
        profiles_dir = Path("moco/profiles")

    typer.echo("Available profiles:")
    if profiles_dir.exists():
        found = False
        for p in sorted(profiles_dir.iterdir()):
            if p.is_dir() and (p / "profile.yaml").exists():
                typer.echo(f"  - {p.name}")
