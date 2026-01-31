#!/usr/bin/env python3
"""Moco CLI"""

# ruff: noqa: E402
import warnings
# ========================================
# 警告の抑制 (インポート前に設定)
# ========================================
# Python 3.9 EOL や SSL 関連の不要な警告を非表示にする
warnings.filterwarnings("ignore", category=FutureWarning)
try:
    # urllib3 の NotOpenSSLWarning はインポート時に発生するため、
    # 警告フィルターを先に設定しておく必要がある
    warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL 1.1.1+.*")
    # Google GenAI の thought_signature 警告を抑制
    warnings.filterwarnings("ignore", message=".*non-text parts in the response.*")
    warnings.filterwarnings("ignore", message=".*thought_signature.*")
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

# 他のモジュールをインポートする前に環境変数を読み込む
_early_load_dotenv()

# ここから通常のインポート
import typer
import time
import sys
from datetime import datetime
from typing import Optional, List
from .ui.theme import ThemeName, THEMES

def init_environment():
    """環境変数の初期化（後方互換性のために残す）"""
    # 既に _early_load_dotenv() で読み込み済みだが、
    # 明示的に呼ばれた場合は再読み込み
    env_path = find_dotenv(usecwd=True) or (Path(__file__).parent.parent.parent / ".env")
    if env_path:
        load_dotenv(env_path, override=True)


def resolve_provider(provider_str: str, model: Optional[str] = None) -> tuple:
    """プロバイダ文字列を解決してLLMProviderとモデル名を返す
    
    Args:
        provider_str: プロバイダ文字列 (例: "gemini", "zai/glm-4.7")
        model: モデル名（既に指定されている場合）
    
    Returns:
        tuple: (LLMProvider, model_name) - 無効なプロバイダの場合は typer.Exit を発生
    """
    from .core.runtime import LLMProvider
    
    # "zai/glm-4.7" のような形式をパース
    provider_name = provider_str
    resolved_model = model
    if "/" in provider_str and model is None:
        parts = provider_str.split("/", 1)
        provider_name = parts[0]
        resolved_model = parts[1]
    
    # プロバイダ名のバリデーションとマッピング
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

# セッション管理用サブコマンド
sessions_app = typer.Typer(help="セッション管理")
app.add_typer(sessions_app, name="sessions")

# Skills 管理用サブコマンド
skills_app = typer.Typer(help="Skills 管理（Claude Skills 互換）")
app.add_typer(skills_app, name="skills")

# タスク管理用サブコマンド
tasks_app = typer.Typer(help="タスク管理")
app.add_typer(tasks_app, name="tasks")


def get_available_profiles() -> List[str]:
    """利用可能なプロファイル一覧を取得"""
    profiles = []
    
    # 1. カレントディレクトリの profiles/
    cwd_profiles = Path.cwd() / "profiles"
    if cwd_profiles.exists():
        for p in cwd_profiles.iterdir():
            if p.is_dir() and (p / "profile.yaml").exists():
                profiles.append(p.name)
    
    # 2. パッケージ内蔵プロファイル
    pkg_profiles = Path(__file__).parent / "profiles"
    if pkg_profiles.exists():
        for p in pkg_profiles.iterdir():
            if p.is_dir() and (p / "profile.yaml").exists():
                if p.name not in profiles:
                    profiles.append(p.name)
    
    return sorted(profiles) if profiles else ["default"]


def complete_profile(incomplete: str) -> List[str]:
    """プロファイル名のタブ補完"""
    profiles = get_available_profiles()
    return [p for p in profiles if p.startswith(incomplete)]


def prompt_profile_selection() -> str:
    """対話的にプロファイルを選択"""
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
    
    # 数字で選択された場合
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(profiles):
            return profiles[idx]
    
    # 名前で選択された場合
    if choice in profiles:
        return choice
    
    return profiles[0]


@app.command()
def run(
    task: str = typer.Argument(..., help="実行するタスク"),
    profile: str = typer.Option("default", "--profile", "-p", help="使用するプロファイル", autocompletion=complete_profile),
    provider: Optional[str] = typer.Option(None, "--provider", "-P", help="LLMプロバイダ (gemini/openai/openrouter/zai) - 省略時は自動選択"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="使用するモデル名 (例: gpt-4o, gemini-2.5-pro, claude-sonnet-4)"),
    stream: bool = typer.Option(False, "--stream/--no-stream", help="ストリーミング出力（デフォルト: オフ）"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="詳細ログ"),
    rich_output: bool = typer.Option(True, "--rich/--plain", help="リッチ出力"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="セッション名（継続 or 新規）"),
    cont: bool = typer.Option(False, "--continue", "-c", help="直前のセッションを継続"),
    auto_retry: int = typer.Option(0, "--auto-retry", help="エラー時の自動リトライ回数"),
    retry_delay: int = typer.Option(3, "--retry-delay", help="リトライ間隔（秒）"),
    show_metrics: bool = typer.Option(False, "--show-metrics", "-M", help="メトリクス表示"),
    theme: ThemeName = typer.Option(ThemeName.DEFAULT, "--theme", help="UIカラーテーマ", case_sensitive=False),
    use_optimizer: bool = typer.Option(False, "--optimizer/--no-optimizer", help="Optimizerによるエージェント自動選択"),
    working_dir: Optional[str] = typer.Option(None, "--working-dir", "-w", help="作業ディレクトリ（subagentに自動伝達）"),
):
    """タスクを実行"""
    if session and cont:
        typer.echo("Error: --session と --continue は同時に指定できません。", err=True)
        raise typer.Exit(code=1)

    from .ui.layout import ui_state
    ui_state.theme = theme

    theme_config = THEMES[theme]

    init_environment()

    # 作業ディレクトリのバリデーションと設定
    if working_dir:
        path = Path(working_dir).resolve()
        if not path.is_dir():
            typer.echo(f"Error: Directory does not exist: {working_dir}", err=True)
            raise typer.Exit(code=1)
        os.environ['MOCO_WORKING_DIRECTORY'] = str(path)

    from .core.orchestrator import Orchestrator
    from .core.llm_provider import get_available_provider

    # プロバイダーの解決（指定なしの場合は優先順位で自動選択）
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

    # セッション管理
    session_id = None
    if cont:
        # 直前のセッションを取得
        sessions = o.session_logger.list_sessions(limit=1)
        if sessions:
            session_id = sessions[0].get("session_id")
            if rich_output:
                console.print(f"[dim]Continuing session: {session_id[:8]}...[/dim]")
        else:
            typer.echo("Warning: 継続するセッションがありません。新規作成します。", err=True)
    elif session:
        # 名前付きセッションを検索または作成
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

    # 実行（リトライ対応）
    start_time = time.time()
    result = None

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
    """エラー種別に応じたヒントを表示"""
    from rich.panel import Panel

    error_str = str(error).lower()
    hints = []

    if "rate limit" in error_str or "429" in error_str:
        hints.append("• レートリミットです。しばらく待ってから再実行してください。")
        hints.append("• --provider を変更してみてください。")
    elif "api key" in error_str or "authentication" in error_str:
        hints.append("• API キーを確認してください。")
        hints.append("• .env ファイルに正しいキーが設定されているか確認。")
    elif "context" in error_str or "token" in error_str:
        hints.append("• プロンプトが長すぎる可能性があります。")
        hints.append("• タスクを分割して実行してみてください。")
    else:
        hints.append("• --verbose オプションで詳細ログを確認してください。")
        hints.append("• --auto-retry でリトライを試してください。")

    console.print(Panel("\n".join(hints), title="💡 Hints", border_style="yellow"))


def _print_result(console, result: str, theme_name: ThemeName = ThemeName.DEFAULT, verbose: bool = False):
    """結果を整形して表示（シンプルテキスト出力）

    Args:
        console: Rich console
        result: 結果文字列
        verbose: True なら全エージェント出力を表示、False なら最後だけ
    """
    import re

    theme = THEMES[theme_name]

    # 最終サマリーを抽出
    final_summary = ""
    if "\n---\n## まとめ" in result:
        parts = result.split("\n---\n## まとめ")
        result = parts[0]
        final_summary = parts[1].strip() if len(parts) > 1 else ""
    elif "\n---\n✅" in result:
        parts = result.split("\n---\n✅")
        result = parts[0]
        final_summary = parts[1].strip() if len(parts) > 1 else ""

    # @agent: 応答 のパターンで分割
    sections = re.split(r'(@[\w-]+):\s*', result)

    if len(sections) > 1:
        if verbose:
            # 全エージェントの出力を表示
            i = 1
            while i < len(sections):
                agent = sections[i]
                content = sections[i + 1].strip() if i + 1 < len(sections) else ""
                if content:
                    # 長すぎる場合は切り詰め
                    lines = content.split('\n')
                    if len(lines) > 30:
                        content = '\n'.join(lines[:30]) + f"\n... ({len(lines) - 30} lines omitted)"
                    console.print(f"\n[bold {theme.thoughts}]{agent}[/]")
                    console.print(content)
                i += 2
        else:
            # 最後のエージェントの結果だけ表示
            last_agent = sections[-2] if len(sections) >= 2 else ""
            last_content = sections[-1].strip() if sections[-1] else ""

            # orchestrator の最終回答は省略しない、他は短縮
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

    # 最終サマリーを表示
    if final_summary:
        console.print(f"\n[bold {theme.result}]✅ まとめ[/]")
        console.print(final_summary)
    elif len(sections) > 1:
        console.print(f"\n[bold {theme.result}]✅ 完了[/]")
    else:
        # 単一の応答
        console.print(result)


@sessions_app.command("list")
def sessions_list(
    limit: int = typer.Option(10, "--limit", "-n", help="表示件数"),
):
    """過去のセッション一覧"""
    from rich.console import Console
    from rich.table import Table
    from .storage.session_logger import SessionLogger
    from .ui.layout import ui_state

    console = Console()
    theme = THEMES.get(ui_state.theme, THEMES[ThemeName.DEFAULT])
    logger = SessionLogger()
    sessions = logger.list_sessions(limit=limit)

    if not sessions:
        console.print("[dim]セッションがありません[/dim]")
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
    session_id: str = typer.Argument(..., help="セッションID（先頭数文字でもOK）"),
):
    """セッションの履歴表示"""
    from rich.console import Console
    from rich.panel import Panel
    from .storage.session_logger import SessionLogger
    from .ui.layout import ui_state

    theme = THEMES[ui_state.theme]
    console = Console()
    logger = SessionLogger()

    # 部分一致でセッションを検索
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
    """利用可能なプロファイル一覧"""
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
                found = True
        if not found:
            typer.echo("  (no profiles found)")
    else:
        typer.echo(f"  Profiles directory not found: {profiles_dir}")


@app.command()
def chat(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="使用するプロファイル", autocompletion=complete_profile),
    provider: Optional[str] = typer.Option(None, "--provider", "-P", help="LLMプロバイダ (gemini/openai/openrouter/zai) - 省略時は自動選択"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="使用するモデル名"),
    stream: Optional[bool] = typer.Option(None, "--stream/--no-stream", help="ストリーミング出力（デフォルト: プロバイダ依存）"),
    subagent_stream: bool = typer.Option(False, "--subagent-stream/--no-subagent-stream", help="サブエージェント本文のストリーミング表示（デフォルト: オフ）"),
    tool_status: bool = typer.Option(True, "--tool-status/--no-tool-status", help="ツール/委譲の短いステータス行を表示（デフォルト: オン）"),
    todo_pane: bool = typer.Option(False, "--todo-pane/--no-todo-pane", help="Todo を右ペインに常時表示（デフォルト: オフ）"),
    async_input: bool = typer.Option(False, "--async-input/--no-async-input", help="処理中も入力を受け付けてキューイング（Gemini CLI風）"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="詳細ログ"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="セッション名（継続 or 新規）"),
    new_session: bool = typer.Option(False, "--new", help="新規セッションを強制開始"),
    theme: ThemeName = typer.Option(ThemeName.DEFAULT, "--theme", help="UIカラーテーマ", case_sensitive=False),
    use_optimizer: bool = typer.Option(False, "--optimizer/--no-optimizer", help="Optimizerによるエージェント自動選択"),
):
    """対話型チャット"""
    from .ui.layout import ui_state
    ui_state.theme = theme
    theme_config = THEMES[theme]

    init_environment()
    from rich.console import Console

    from .core.orchestrator import Orchestrator
    from .core.llm_provider import get_available_provider
    from .core.runtime import _safe_stream_print

    console = Console()
    stream_flags = {"show_subagent_stream": subagent_stream, "show_tool_status": tool_status}
    # Track whether we have printed any streamed text without a newline recently.
    # Used to avoid mixing tool logs into the middle of a line.
    stream_state = {"mid_line": False}

    # prompt_toolkit printing helpers (used in --async-input mode)
    pt_ansi_print = None

    # Async-input mode (Gemini CLI style):
    # - allow typing next prompts while the current one is processing
    # - enqueue prompts and execute sequentially in a worker thread
    if async_input and todo_pane:
        console.print("[yellow]--async-input is currently incompatible with --todo-pane. Disabling --async-input.[/yellow]")
        async_input = False
    if async_input:
        import sys
        if not sys.stdin.isatty():
            console.print("[yellow]--async-input requires an interactive TTY stdin. Disabling --async-input.[/yellow]")
            async_input = False

    # Optional: side pane for Todos (Rich Live layout)
    pane_state = {
        "enabled": bool(todo_pane),
        "live": None,
        "layout": None,
        "lines": [],
        "max_lines": 500,
    }

    def _pane_append(line: str) -> None:
        if not pane_state["enabled"]:
            return
        if line is None:
            return
        s = str(line)
        if not s:
            return
        # Split to keep rendering stable
        parts = s.splitlines() or [s]
        pane_state["lines"].extend(parts)
        # Trim
        if len(pane_state["lines"]) > pane_state["max_lines"]:
            pane_state["lines"] = pane_state["lines"][-pane_state["max_lines"] :]

    def _pane_update_chat_panel() -> None:
        if not pane_state["enabled"]:
            return
        live = pane_state.get("live")
        layout = pane_state.get("layout")
        if not live or not layout:
            return
        try:
            from rich.panel import Panel
            from rich.text import Text
            from rich import box

            # Auto-follow: render only the bottom-most lines that fit in the panel.
            # (If we render the whole buffer, Rich will show from the top and the latest
            # conversation scrolls out of view.)
            try:
                chat_w = max(20, int(getattr(layout["chat"], "size", None).width or console.size.width) - 4)
                chat_h = max(6, int(getattr(layout["chat"], "size", None).height or console.size.height) - 4)
            except Exception:
                chat_w = max(20, console.size.width - 4)
                chat_h = max(6, console.size.height - 4)

            # Build visible lines from bottom up, accounting for wrapping.
            visible_lines = []
            used_rows = 0
            for ln in reversed(pane_state["lines"][-pane_state["max_lines"] :]):
                try:
                    t = Text.from_markup(ln)
                    plain = t.plain
                except Exception:
                    plain = str(ln)
                # Approximate wrap rows
                rows = max(1, (len(plain) + max(1, chat_w) - 1) // max(1, chat_w))
                if used_rows + rows > chat_h:
                    break
                visible_lines.append(ln)
                used_rows += rows
            visible_lines.reverse()

            text = Text()
            for ln in visible_lines:
                try:
                    text.append_text(Text.from_markup(ln))
                except Exception:
                    text.append(ln)
                text.append("\n")

            layout["chat"].update(
                Panel(
                    text,
                    title="Chat",
                    border_style=theme_config.status,
                    box=box.ROUNDED,
                )
            )
            live.refresh()
        except Exception:
            return

    def _pane_update_todo_panel(session_id: Optional[str]) -> None:
        if not pane_state["enabled"]:
            return
        live = pane_state.get("live")
        layout = pane_state.get("layout")
        if not live or not layout:
            return
        try:
            from rich.panel import Panel
            from rich.text import Text
            from rich import box
            from moco.tools.todo import todoread_all, set_current_session

            if session_id:
                set_current_session(session_id)
            raw = todoread_all()
            todo_text = Text(raw or "(no todos)", style="default")
            layout["todo"].update(
                Panel(
                    todo_text,
                    title="Todos",
                    border_style=theme_config.tools,
                    box=box.ROUNDED,
                )
            )
            live.refresh()
        except Exception as e:
            try:
                from rich.panel import Panel
                from rich.text import Text
                from rich import box

                layout["todo"].update(
                    Panel(
                        Text(f"(todo pane error) {e}", style="dim"),
                        title="Todos",
                        border_style=theme_config.tools,
                        box=box.ROUNDED,
                    )
                )
                live.refresh()
            except Exception:
                return

    # Streaming callback for CLI:
    # - tool/delegate logs are printed elsewhere (keep as-is)
    # - print streamed chunks only for orchestrator by default
    def progress_callback(
        event_type: str,
        content: str = None,
        agent_name: str = None,
        **kwargs
    ):
        """
        CLI progress callback.

        Notes:
        - We keep chunk streaming behavior as-is.
        - We additionally surface tool/delegate completion so users can see whether
          write_file/edit_file actually succeeded (or failed).
        """
        # ANSI color code mapping for async-input mode
        _ANSI_COLORS = {
            "black": "30", "red": "31", "green": "32", "yellow": "33",
            "blue": "34", "magenta": "35", "cyan": "36", "white": "37",
            "bright_black": "90", "bright_red": "91", "bright_green": "92",
            "bright_yellow": "93", "bright_blue": "94", "bright_magenta": "95",
            "bright_cyan": "96", "bright_white": "97", "grey50": "90",
        }

        def _get_ansi_code(style: str) -> str:
            """Extract ANSI code from Rich style string."""
            codes = []
            if "bold" in style:
                codes.append("1")
            for color_name, code in _ANSI_COLORS.items():
                if color_name in style:
                    codes.append(code)
                    break
            return ";".join(codes) if codes else "0"

        def _safe_stream_print_styled(text: str, style: str) -> None:
            """Print streamed text with color without breaking streaming."""
            if not text:
                return
            try:
                from rich.text import Text
                if async_input:
                    # Use ANSI escape codes for color in async-input mode
                    ansi_code = _get_ansi_code(style)
                    if ansi_code and ansi_code != "0":
                        _safe_stream_print(f"\x1b[{ansi_code}m{text}\x1b[0m")
                    else:
                        _safe_stream_print(text)
                else:
                    console.print(Text(text, style=style), end="")
            except BrokenPipeError:
                return
            except OSError as e:
                if getattr(e, "errno", None) == 32:
                    return
                _safe_stream_print(text)
            except Exception:
                _safe_stream_print(text)

        # Start marker for orchestrator output (helps distinguish from user input)
        if event_type == "start" and (agent_name or "") == "orchestrator":
            stream_state["thinking_shown"] = False  # Reset thinking flag for new response
            stream_state["thinking_ended"] = False
            if pane_state["enabled"]:
                _pane_append("[bold]🤖[/bold] ")
                _pane_update_chat_panel()
                return
            if stream_state.get("mid_line"):
                _safe_stream_print("\n")
                stream_state["mid_line"] = False
            _safe_stream_print_styled("🤖 ", f"bold {theme_config.result}")
            stream_state["mid_line"] = True
            return

        # Thinking/reasoning content (verbose mode only)
        if event_type == "thinking" and content and verbose:
            if pane_state["enabled"]:
                # Show thinking in pane with dimmed style
                if not stream_state.get("thinking_shown"):
                    _pane_append("[dim]💭 Thinking...[/dim]")
                    stream_state["thinking_shown"] = True
                # Don't show full thinking content in pane (too verbose)
                return
            # CLI direct output
            if not stream_state.get("thinking_shown"):
                if async_input and pt_ansi_print:
                    pt_ansi_print("\x1b[2m💭 Thinking...\x1b[0m")
                else:
                    console.print("[dim]💭 Thinking...[/dim]")
                stream_state["thinking_shown"] = True
            # Show thinking content (dimmed)
            if async_input and pt_ansi_print:
                pt_ansi_print(f"\x1b[2m{content}\x1b[0m")
            else:
                console.print(f"[dim]{content}[/dim]", end="")
            return

        # Streamed text chunks
        if event_type == "chunk" and content:
            # End thinking display if it was shown
            if stream_state.get("thinking_shown") and not stream_state.get("thinking_ended"):
                if not pane_state["enabled"]:
                    _safe_stream_print("\n")  # Newline after thinking
                stream_state["thinking_ended"] = True
            name = agent_name or ""
            if name == "orchestrator" or stream_flags.get("show_subagent_stream"):
                if pane_state["enabled"]:
                    # Append to last line (create if needed)
                    if not pane_state["lines"]:
                        pane_state["lines"].append("🤖 ")
                    chunk = str(content)
                    parts = chunk.split("\n")
                    # First part appends to current last line
                    pane_state["lines"][-1] = (pane_state["lines"][-1] or "") + parts[0]
                    # Remaining parts become new lines
                    for p in parts[1:]:
                        pane_state["lines"].append(p)
                    # Trim
                    if len(pane_state["lines"]) > pane_state["max_lines"]:
                        pane_state["lines"] = pane_state["lines"][-pane_state["max_lines"] :]
                    _pane_update_chat_panel()
                    return
                # Color the assistant output to visually separate it from the user's input line.
                _safe_stream_print_styled(content, theme_config.result)
                stream_state["mid_line"] = True
            return

        # Ensure newline after orchestrator main response
        if event_type == "done":
            if (agent_name or "") == "orchestrator":
                if pane_state["enabled"]:
                    _pane_append("")  # spacing
                    _pane_update_chat_panel()
                    return
                _safe_stream_print("\n")
                stream_state["mid_line"] = False
            return

        # Delegation status (running/completed)
        if event_type == "delegate":
            if not stream_flags.get("show_tool_status", True):
                return
            status = (kwargs.get("status") or "").lower()
            name = kwargs.get("name") or agent_name or ""
            detail = (kwargs.get("detail") or "").strip()
            if name and not str(name).startswith("@"):
                name = f"@{name}"
            if pane_state["enabled"]:
                # Keep default output compact: show only completion unless verbose.
                if status == "running" and verbose:
                    _pane_append(f"[dim]→ {name}[/dim]")
                elif status == "completed":
                    _pane_append(f"[green]✓ {name}[/green]")
                else:
                    if verbose:
                        _pane_append(f"[dim]{status or 'delegate'} {name}[/dim]")
                _pane_update_chat_panel()
                return
            # If we're mid-stream, start a fresh line to keep logs readable.
            if stream_state.get("mid_line"):
                _safe_stream_print("\n")
                stream_state["mid_line"] = False
            if status == "running":
                if async_input and pt_ansi_print:
                    # Show agent + truncated task text with colors (Gemini CLI style)
                    msg = f"\x1b[2m→\x1b[0m \x1b[36m{name}\x1b[0m"
                    if detail:
                        d = detail.replace("\n", " ").strip()
                        if len(d) > 90:
                            d = d[:87] + "..."
                        msg += f" \x1b[2m{d}\x1b[0m"
                    pt_ansi_print(msg)
                else:
                    console.print(f"[dim]→ {name}[/dim]")
            elif status == "completed":
                if async_input and pt_ansi_print:
                    pt_ansi_print(f"\x1b[32m✓\x1b[0m \x1b[36m{name}\x1b[0m")
                else:
                    console.print(f"[green]✓ {name}[/green]")
            else:
                if async_input and pt_ansi_print:
                    pt_ansi_print(f"\x1b[2m{status or 'delegate'}\x1b[0m \x1b[36m{name}\x1b[0m")
                else:
                    console.print(f"[dim]{status or 'delegate'} {name}[/dim]")
            return

        # Tool status: show running + success/error so file ops are verifiable in-chat.
        if event_type == "tool":
            if not stream_flags.get("show_tool_status", True):
                return
            status = (kwargs.get("status") or "").lower()
            tool_name = kwargs.get("tool_name") or kwargs.get("tool") or ""
            detail = kwargs.get("detail") or ""
            result = kwargs.get("result")

            if pane_state["enabled"]:
                # Default: one line per tool (completed only). Running line only in verbose.
                if status == "running":
                    if verbose:
                        line = tool_name or "tool"
                        if detail:
                            line += f" → {detail}"
                        _pane_append(f"[dim]→ {line}[/dim]")
                        _pane_update_chat_panel()
                    return
                if status != "completed":
                    return

                result_str = "" if result is None else str(result)
                is_error = result_str.startswith("Error") or result_str.startswith("ERROR:")
                line = tool_name or "tool"
                if detail:
                    line += f" → {detail}"
                # (No long summary here; keep compact. Verbose summary stays in normal mode.)
                if is_error:
                    _pane_append(f"[red]✗ {line}[/red]")
                else:
                    _pane_append(f"[green]✓ {line}[/green]")
                _pane_update_chat_panel()
                # Refresh todo pane immediately when todos might have changed.
                if tool_name in ("todowrite", "todoread", "todoread_all"):
                    _pane_update_todo_panel(command_context.get("session_id"))
                return

            if stream_state.get("mid_line"):
                _safe_stream_print("\n")
                stream_state["mid_line"] = False

            # Running line (start)
            if status == "running":
                # Default: keep tool-status output compact (one line per tool).
                # Show the "running" line only in verbose mode.
                if verbose:
                    line = tool_name or "tool"
                    if detail:
                        line += f" → {detail}"
                    if async_input and pt_ansi_print:
                        pt_ansi_print(f"\x1b[2m→\x1b[0m \x1b[36m{line}\x1b[0m")
                    else:
                        console.print(f"[dim]→ {line}[/dim]")
                return

            if status != "completed":
                return

            # Determine success/failure from result text
            result_str = "" if result is None else str(result)
            is_error = result_str.startswith("Error") or result_str.startswith("ERROR:")

            # Build a concise line, e.g. "✓ write_file → MOBILE_SPEC.md"
            line = tool_name or "tool"
            if detail:
                line += f" → {detail}"
            # Only show the (potentially long) tool result summary in verbose mode.
            # This keeps default tool-status output short (no "Successfully edited ... (+22)" etc.).
            if verbose and result_str:
                summary = result_str.splitlines()[0].strip()
                if len(summary) > 140:
                    summary = summary[:137] + "..."
                if summary:
                    line += f" ({summary})"

            if is_error:
                if async_input and pt_ansi_print:
                    pt_ansi_print(f"\x1b[31m✗\x1b[0m \x1b[36m{line}\x1b[0m")
                else:
                    console.print(f"[red]✗ {line}[/red]")
            else:
                if async_input and pt_ansi_print:
                    pt_ansi_print(f"\x1b[32m✓\x1b[0m \x1b[36m{line}\x1b[0m")
                else:
                    console.print(f"[green]✓ {line}[/green]")
            return

    # プロファイルの解決（指定なしの場合は対話選択）
    if profile is None:
        profile = prompt_profile_selection()

    # プロバイダーの解決（指定なしの場合は優先順位で自動選択）
    if provider is None:
        provider = get_available_provider()

    provider_enum, model = resolve_provider(provider, model)
    # デフォルトのストリーム挙動:
    # - ZAI: ツール呼び出しがストリーミングで不安定なためデフォルトOFF
    # - その他: デフォルトON
    # NOTE: LLMProvider is a simple constants class (strings), not Enum.
    provider_name = getattr(provider_enum, "value", provider_enum)
    if stream is None:
        stream = (provider_name != "zai")

    with console.status(f"[bold cyan]Initializing Orchestrator ({profile})...[/]"):
        o = Orchestrator(
            profile=profile,
            provider=provider_enum,
            model=model,
            stream=stream,
            verbose=verbose,
            use_optimizer=use_optimizer,
            progress_callback=progress_callback if stream else None,
        )

    # Context for slash commands
    command_context = {
        'orchestrator': o,
        'console': console,
        'verbose': verbose,
        'stream_flags': stream_flags,
    }

    # セッション管理
    session_id = None
    if not new_session:
        if session:
            # 名前付きセッションを検索
            sessions = o.session_logger.list_sessions(limit=50)
            for s in sessions:
                if s.get("title", "").endswith(f"[{session}]"):
                    session_id = s.get("session_id")
                    console.print(f"[dim]Resuming session: {session}[/dim]")
                    break
        else:
            # 最新のセッションを取得（デフォルトの挙動）
            sessions = o.session_logger.list_sessions(limit=1)
            if sessions:
                session_id = sessions[0].get("session_id")
                console.print(f"[dim]Using latest session: {session_id[:8]}...[/dim]")

    if not session_id:
        title = "CLI Chat" + (f" [{session}]" if session else "")
        session_id = o.create_session(title=title)
        console.print(f"[dim]New session: {session_id[:8]}...[/dim]")

    command_context['session_id'] = session_id
    # Optional: allow slash commands to interact with the todo-pane
    # (so `/todo` can refresh the right pane without printing raw text to the terminal).
    command_context["pane_enabled"] = bool(pane_state.get("enabled"))
    command_context["pane_append"] = _pane_append
    command_context["pane_refresh_chat"] = _pane_update_chat_panel
    command_context["pane_refresh_todo"] = lambda: _pane_update_todo_panel(command_context.get("session_id"))

    # --- Dashboard Display ---
    from .ui.welcome import show_welcome_dashboard
    show_welcome_dashboard(o, theme_config)
    # -------------------------

    # If todo pane is enabled, set up a 2-pane Rich layout
    live_ctx = None
    if todo_pane:
        try:
            from rich.layout import Layout
            from rich.live import Live
            from rich.panel import Panel
            from rich.text import Text
            from rich import box
            from moco.tools.todo import set_current_session

            set_current_session(session_id)

            root = Layout(name="root")
            width = getattr(console, "size", None).width if getattr(console, "size", None) else 120

            if width >= 120:
                root.split_row(
                    Layout(name="chat", ratio=3),
                    Layout(name="todo", ratio=1, minimum_size=36),
                )
            else:
                # Fallback for narrow terminals: place todo below
                root.split_column(
                    Layout(name="chat", ratio=3),
                    Layout(name="todo", ratio=1),
                )

            pane_state["enabled"] = True
            pane_state["layout"] = root

            # Initial render
            root["chat"].update(
                Panel(Text("(waiting for output...)", style="dim"), title="Chat", border_style=theme_config.status, box=box.ROUNDED)
            )
            root["todo"].update(
                Panel(Text("(loading...)", style="dim"), title="Todos", border_style=theme_config.tools, box=box.ROUNDED)
            )

            live_ctx = Live(root, console=console, auto_refresh=False)
            live_ctx.__enter__()
            pane_state["live"] = live_ctx

            _pane_update_todo_panel(session_id)
            _pane_update_chat_panel()
        except Exception as e:
            pane_state["enabled"] = False
            pane_state["live"] = None
            pane_state["layout"] = None
            console.print(f"[yellow]Todo pane disabled (failed to initialize): {e}[/yellow]")

    # --- スラッシュコマンド対応 ---
    from .cli_commands import handle_slash_command
    from .cancellation import create_cancel_event, request_cancel, clear_cancel_event, OperationCancelled
    # ---

    try:
        # If async_input is enabled, run orchestration in a background worker and keep reading input.
        if async_input:
            try:
                from prompt_toolkit import PromptSession
                from prompt_toolkit.patch_stdout import patch_stdout
                from prompt_toolkit.key_binding import KeyBindings
            except Exception as e:
                console.print(f"[yellow]--async-input requires prompt_toolkit. ({e})[/yellow]")
                async_input = False

        if async_input:
            import threading
            import queue
            from datetime import datetime as _dt
            from prompt_toolkit.shortcuts import print_formatted_text
            from prompt_toolkit.formatted_text import ANSI

            # Tell slash commands to avoid Rich markup (prevents raw ANSI escapes in some terminals).
            command_context["plain_output"] = True
            command_context["plain_print"] = print_formatted_text

            # Use ANSI-aware printing for progress output (tool/delegate) to keep colors without mojibake.
            def _pt_ansi_print(s: str) -> None:
                try:
                    print_formatted_text(ANSI(s))
                except Exception:
                    # fall back to plain stdout
                    _safe_stream_print(str(s) + "\n")

            pt_ansi_print = _pt_ansi_print

            pending: "queue.Queue[str | None]" = queue.Queue()
            busy_lock = threading.Lock()
            busy = {"running": False}
            stop_requested = {"stop": False}

            def _set_busy(val: bool) -> None:
                with busy_lock:
                    busy["running"] = val

            def _is_busy() -> bool:
                with busy_lock:
                    return bool(busy["running"])

            def _worker() -> None:
                while True:
                    item = pending.get()
                    if item is None:
                        return

                    _set_busy(True)
                    try:
                        create_cancel_event(session_id)
                        result = o.run_sync(item, session_id)
                        if result and not stream:
                            # Prefer plain output in async-input mode to avoid ANSI artifacts.
                            print_formatted_text("")
                            print_formatted_text(result)
                            print_formatted_text("")
                    except KeyboardInterrupt:
                        request_cancel(session_id)
                        print_formatted_text("\nInterrupted.")
                    except OperationCancelled:
                        print_formatted_text("\nOperation cancelled.")
                    except Exception as e:  # noqa: BLE001
                        print_formatted_text(f"Error: {e}")
                    finally:
                        clear_cancel_event(session_id)
                        _set_busy(False)
                        if stop_requested["stop"]:
                            return

            worker = threading.Thread(target=_worker, daemon=True)
            worker.start()

            kb = KeyBindings()

            @kb.add("c-c")
            def _(event):  # noqa: ANN001
                # If running, cancel current task; otherwise exit.
                if _is_busy():
                    request_cancel(session_id)
                    print_formatted_text("(cancel requested)")
                else:
                    stop_requested["stop"] = True
                    pending.put(None)
                    event.app.exit()

            prompt = PromptSession(key_bindings=kb)

            with patch_stdout():
                while True:
                    # 最新のテーマ設定を反映
                    theme_config = THEMES[ui_state.theme]

                    try:
                        text = prompt.prompt("> ")
                    except (EOFError, KeyboardInterrupt):
                        # EOF / Ctrl+C while idle -> exit
                        stop_requested["stop"] = True
                        pending.put(None)
                        break

                    if not (text or "").strip():
                        continue

                    # Slash commands are processed immediately in the main thread.
                    if text.strip().startswith("/"):
                        # Avoid session-changing commands while busy (they can desync current run)
                        if _is_busy() and text.strip().split()[0].lower() in ("/profile", "/session", "/clear"):
                            print_formatted_text("That command is blocked while a task is running. Try again after completion.")
                            continue

                        if not handle_slash_command(text, command_context):
                            stop_requested["stop"] = True
                            pending.put(None)
                            break

                        if "pending_prompt" in command_context:
                            text = command_context.pop("pending_prompt")
                        else:
                            session_id = command_context["session_id"]
                            continue

                    lowered = text.strip().lower()
                    if lowered in ("exit", "quit"):
                        stop_requested["stop"] = True
                        # Ask current run to stop, then exit after worker finishes.
                        if _is_busy():
                            request_cancel(session_id)
                        pending.put(None)
                        break

                    # Enqueue normal prompts.
                    pending.put(text)
                    qsize = pending.qsize()
                    if _is_busy() or qsize > 0:
                        # Plain text to avoid ANSI escape artifacts in some terminals/recorders
                        print_formatted_text(f"(queued {qsize} @ {_dt.now().strftime('%H:%M:%S')})")

            # Wait briefly for worker to exit (best-effort)
            worker.join(timeout=2)
            return

        while True:
            # 最新のテーマ設定を反映
            theme_config = THEMES[ui_state.theme]

            try:
                if pane_state["enabled"]:
                    _pane_update_todo_panel(command_context.get("session_id"))
                    _pane_update_chat_panel()
                # Liveが有効だと入力プロンプトが再描画で見えなくなるので、
                # 入力中は一時的に Live を停止して端末の制御を戻す。
                if pane_state["enabled"] and live_ctx is not None:
                    try:
                        live_ctx.stop()
                    except Exception:
                        pass

                text = console.input(f"[bold {theme_config.status}]> [/bold {theme_config.status}]")

                # 入力が終わったら Live を再開し、左ペインにもユーザー入力を残す
                if pane_state["enabled"] and live_ctx is not None:
                    try:
                        live_ctx.start()
                    except Exception:
                        pass
                    if text and text.strip():
                        _pane_append(f"[bold {theme_config.status}]User:[/bold {theme_config.status}] {text.strip()}")
                        _pane_update_chat_panel()
            except EOFError:
                break

            if not text.strip():
                continue

            # スラッシュコマンド判定
            if text.strip().startswith('/'):
                if not handle_slash_command(text, command_context):
                    raise typer.Exit(code=0)

                # カスタムコマンド等で pending_prompt がセットされた場合、それを通常の入力として扱う
                if 'pending_prompt' in command_context:
                    text = command_context.pop('pending_prompt')
                else:
                    # handle_slash_command 内で session_id が更新されている可能性がある
                    session_id = command_context['session_id']
                    continue

            lowered = text.strip().lower()
            if lowered in ("exit", "quit"):
                console.print("[dim]Bye![/dim]")
                raise typer.Exit(code=0)

            try:
                create_cancel_event(session_id)
                # シンプルにrun_syncを呼ぶだけ（streaming時はruntimeが直接出力）
                reply = o.run_sync(text, session_id)
            except KeyboardInterrupt:
                request_cancel(session_id)
                console.print("\n[yellow]Interrupted. Type 'exit' to quit or continue with a new prompt.[/yellow]")
                continue
            except OperationCancelled:
                console.print("\n[yellow]Operation cancelled.[/yellow]")
                continue
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]Error: {e}[/red]")
                continue
            finally:
                clear_cancel_event(session_id)

            # stream 時は Live または runtime の標準出力で表示済み（ここで二重表示しない）
            if reply and not stream:
                console.print()
                _print_result(console, reply, theme_name=ui_state.theme, verbose=verbose)
                console.print()
    except KeyboardInterrupt:
        console.print("\n[dim]Bye![/dim]")
    finally:
        if live_ctx is not None:
            try:
                live_ctx.__exit__(None, None, None)
            except Exception:
                pass


# ========== Skills Commands ==========

@skills_app.command("list")
def skills_list(
    profile: str = typer.Option("default", "--profile", "-p", help="プロファイル"),
):
    """インストール済み Skills 一覧"""
    from rich.console import Console
    from rich.table import Table
    from .tools.skill_loader import SkillLoader
    from .ui.layout import ui_state

    console = Console()
    theme = THEMES.get(ui_state.theme, THEMES[ThemeName.DEFAULT])
    loader = SkillLoader(profile=profile)
    skills = loader.list_installed_skills()

    if not skills:
        console.print(f"[dim]No skills installed in profile '{profile}'[/dim]")
        console.print("[dim]Try: moco skills sync anthropics[/dim]")
        return

    table = Table(title=f"Skills ({profile})", border_style=theme.tools)
    table.add_column("Name", style=theme.tools)
    table.add_column("Description", style=theme.result)
    table.add_column("Version", style=theme.status)
    table.add_column("Source", style="dim")

    for s in skills:
        table.add_row(
            s["name"],
            s["description"][:50] + ("..." if len(s["description"]) > 50 else ""),
            s["version"],
            s["source"][:30] + ("..." if len(s["source"]) > 30 else ""),
        )

    console.print(table)


@skills_app.command("install")
def skills_install(
    repo: str = typer.Argument(..., help="GitHub リポジトリ (例: anthropics/skills)"),
    skill_name: Optional[str] = typer.Argument(None, help="スキル名（省略時は全スキル）"),
    profile: str = typer.Option("default", "--profile", "-p", help="プロファイル"),
    branch: str = typer.Option("main", "--branch", "-b", help="ブランチ"),
):
    """GitHub から Skills をインストール"""
    from rich.console import Console
    from .tools.skill_loader import SkillLoader

    console = Console()
    loader = SkillLoader(profile=profile)

    if skill_name:
        # 単一スキルをインストール
        console.print(f"[dim]Installing skill '{skill_name}' from {repo}...[/dim]")
        success, message = loader.install_skill_from_github(repo, skill_name, branch)
        if success:
            console.print(f"[green]✅ {message}[/green]")
        else:
            console.print(f"[red]❌ {message}[/red]")
            raise typer.Exit(code=1)
    else:
        # 全スキルをインストール
        console.print(f"[dim]Installing all skills from {repo}...[/dim]")
        count, names = loader.install_skills_from_repo(repo, branch)
        if count > 0:
            console.print(f"[green]✅ Installed {count} skills:[/green]")
            for name in sorted(names):
                console.print(f"  - {name}")
        else:
            console.print("[yellow]No skills found in repository[/yellow]")


@skills_app.command("sync")
def skills_sync(
    registry: str = typer.Argument("anthropics", help="レジストリ名 (anthropics/community/claude-code/collection)"),
    profile: str = typer.Option("default", "--profile", "-p", help="プロファイル"),
):
    """レジストリから Skills を同期"""
    from rich.console import Console
    from .tools.skill_loader import SkillLoader

    console = Console()
    loader = SkillLoader(profile=profile)

    console.print(f"[dim]Syncing skills from '{registry}' registry...[/dim]")
    count, names = loader.sync_from_registry(registry)

    if count > 0:
        console.print(f"[green]✅ Synced {count} skills:[/green]")
        for name in sorted(names)[:20]:  # 最初の20件だけ表示
            console.print(f"  - {name}")
        if len(names) > 20:
            console.print(f"  ... and {len(names) - 20} more")
    else:
        console.print("[yellow]No skills found or sync failed[/yellow]")


@skills_app.command("uninstall")
def skills_uninstall(
    skill_name: str = typer.Argument(..., help="スキル名"),
    profile: str = typer.Option("default", "--profile", "-p", help="プロファイル"),
):
    """Skill をアンインストール"""
    from rich.console import Console
    from .tools.skill_loader import SkillLoader

    console = Console()
    loader = SkillLoader(profile=profile)

    success, message = loader.uninstall_skill(skill_name)
    if success:
        console.print(f"[green]✅ {message}[/green]")
    else:
        console.print(f"[red]❌ {message}[/red]")
        raise typer.Exit(code=1)


@skills_app.command("search")
def skills_search(
    query: str = typer.Argument(..., help="検索クエリ"),
    profile: str = typer.Option("default", "--profile", "-p", help="プロファイル"),
):
    """インストール済み Skills を検索"""
    from rich.console import Console
    from rich.table import Table
    from .tools.skill_loader import SkillLoader
    from .ui.layout import ui_state

    console = Console()
    theme = THEMES.get(ui_state.theme, THEMES[ThemeName.DEFAULT])
    loader = SkillLoader(profile=profile)
    results = loader.search_skills(query)

    if not results:
        console.print(f"[dim]No skills matching '{query}'[/dim]")
        return

    table = Table(title=f"Search: {query}", border_style=theme.tools)
    table.add_column("Name", style=theme.tools)
    table.add_column("Description", style=theme.result)
    table.add_column("Triggers", style="dim")

    for s in results:
        table.add_row(
            s["name"],
            s["description"][:50],
            ", ".join(s["triggers"][:3]),
        )

    console.print(table)


@skills_app.command("info")
def skills_info():
    """Skills レジストリ情報"""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    table = Table(title="Available Registries", border_style="cyan")
    table.add_column("Name", style="cyan")
    table.add_column("Repository", style="white")
    table.add_column("Description", style="dim")

    registries = [
        ("anthropics", "anthropics/skills", "公式 Claude Skills"),
        ("community", "alirezarezvani/claude-skills", "コミュニティ Skills"),
        ("remotion", "remotion-dev/skills", "Remotion 動画生成 Skills"),
        ("claude-code", "daymade/claude-code-skills", "Claude Code 特化"),
        ("collection", "abubakarsiddik31/claude-skills-collection", "キュレーション集"),
    ]

    for name, repo, desc in registries:
        table.add_row(name, repo, desc)

    console.print(table)
    console.print()
    console.print("[dim]Usage: moco skills sync <registry-name>[/dim]")
    console.print("[dim]Example: moco skills sync anthropics[/dim]")


@app.command("version")
def version(
    detailed: bool = typer.Option(False, "--detailed", "-d", help="依存関係のバージョンも表示"),
):
    """バージョン表示"""
    from importlib.metadata import version as get_version, PackageNotFoundError
    from rich.console import Console
    from rich.table import Table
    
    console = Console()
    
    try:
        v = get_version("moco")
    except PackageNotFoundError:
        v = "0.2.0"
    
    if not detailed:
        typer.echo(f"Moco v{v}")
        return
    
    # 詳細表示モード
    table = Table(title=f"Moco v{v}", border_style="cyan")
    table.add_column("Package", style="cyan")
    table.add_column("Version")
    
    # コア依存関係
    core_deps = [
        "typer",
        "rich",
        "pydantic",
        "pyyaml",
        "fastapi",
        "uvicorn",
        "sqlmodel",
        "alembic",
        "httpx",
        "aiohttp",
        "openai",
        "google-generativeai",
        "google-genai",
        "tiktoken",
        "numpy",
        "faiss-cpu",
        "python-dotenv",
        "PyGithub",
        "networkx",
        "prompt_toolkit",
    ]
    
    for dep in core_deps:
        try:
            dep_version = get_version(dep)
            table.add_row(dep, dep_version)
        except PackageNotFoundError:
            table.add_row(dep, "[dim]not installed[/dim]")
    
    console.print(table)


# --- Tasks Subcommands ---

@tasks_app.command("run")
def tasks_run(
    task: str = typer.Argument(..., help="実行するタスク内容"),
    profile: str = typer.Option("default", "--profile", "-p", help="プロファイル", autocompletion=complete_profile),
    provider: Optional[str] = typer.Option(None, "--provider", "-P", help="プロバイダ - 省略時は自動選択"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="使用するモデル名"),
    working_dir: Optional[str] = typer.Option(None, "--working-dir", "-w", help="作業ディレクトリ"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="継続するセッションID"),
):
    """タスクをバックグラウンドで実行"""
    init_environment()
    from .storage.task_store import TaskStore
    from .core.task_runner import TaskRunner
    from .core.llm_provider import get_available_provider
    import os

    # プロバイダーの解決（指定なしの場合は優先順位で自動選択）
    if provider is None:
        provider = get_available_provider()
    
    # "zai/glm-4.7" のような形式をパース
    resolved_provider = provider
    resolved_model = model
    if "/" in provider and model is None:
        parts = provider.split("/", 1)
        resolved_provider = parts[0]
        resolved_model = parts[1]

    # 作業ディレクトリを絶対パスに解決
    resolved_working_dir = None
    if working_dir:
        resolved_working_dir = os.path.abspath(working_dir)

    store = TaskStore()
    task_id = store.add_task(task, profile, resolved_provider, resolved_working_dir)

    runner = TaskRunner(store)
    runner.run_task(task_id, profile, task, resolved_working_dir, resolved_provider, resolved_model)

    typer.echo(f"Task started: {task_id}")
    if session:
        typer.echo(f"Continuing session: {session}")


@tasks_app.command("list")
def tasks_list(
    limit: int = typer.Option(20, "--limit", "-l", help="表示件数"),
):
    """タスク一覧（経過時間付き）"""
    from .storage.task_store import TaskStore
    from rich.console import Console
    from rich.table import Table
    from datetime import datetime

    store = TaskStore()
    tasks = store.list_tasks(limit=limit)

    console = Console()

    def truncate(text: str, max_len: int = 35) -> str:
        """説明文を短く切り詰める（最初の行のみ）"""
        first_line = text.split('\n')[0].strip()
        if len(first_line) > max_len:
            return first_line[:max_len] + "..."
        return first_line

    def format_duration(start_str: str, end_str: str = None) -> str:
        """経過時間をフォーマット"""
        if not start_str:
            return "-"
        try:
            start = datetime.fromisoformat(start_str)
            end = datetime.fromisoformat(end_str) if end_str else datetime.now()
            delta = end - start
            total_seconds = int(delta.total_seconds())

            if total_seconds < 60:
                return f"{total_seconds}s"
            elif total_seconds < 3600:
                mins = total_seconds // 60
                secs = total_seconds % 60
                return f"{mins}m {secs}s"
            else:
                hours = total_seconds // 3600
                mins = (total_seconds % 3600) // 60
                return f"{hours}h {mins}m"
        except Exception:
            return "-"

    # サマリー
    running = sum(1 for t in tasks if t["status"] == "running")
    completed = sum(1 for t in tasks if t["status"] == "completed")
    failed = sum(1 for t in tasks if t["status"] == "failed")

    console.print(f"\n🔄 Running: [yellow]{running}[/]  ✅ Done: [green]{completed}[/]  ❌ Failed: [red]{failed}[/]\n")

    table = Table(title="Task List")
    table.add_column("", width=2)  # アイコン
    table.add_column("ID", style="cyan", no_wrap=True, width=10)
    table.add_column("Description", max_width=35, no_wrap=True)
    table.add_column("Status", width=10)
    table.add_column("Duration", width=10, justify="right")
    table.add_column("Created", no_wrap=True, width=16)

    for t in tasks:
        status = t["status"]

        # アイコンと色
        if status == "running":
            icon = "🔄"
            color = "yellow"
        elif status == "completed":
            icon = "✅"
            color = "green"
        elif status == "failed":
            icon = "❌"
            color = "red"
        elif status == "pending":
            icon = "⏳"
            color = "dim"
        elif status == "cancelled":
            icon = "🚫"
            color = "dim"
        else:
            icon = "❓"
            color = "white"

        # 経過時間
        if status == "running":
            duration = format_duration(t["started_at"])
        elif status in ("completed", "failed"):
            duration = format_duration(t["started_at"], t["completed_at"])
        else:
            duration = "-"

        table.add_row(
            icon,
            t["task_id"][:10],
            truncate(t["task_description"]),
            f"[{color}]{status}[/]",
            f"[{color}]{duration}[/]",
            t["created_at"][5:16].replace("T", " ")  # MM-DD HH:MM
        )

    console.print(table)


@tasks_app.command("status")
def tasks_status():
    """リアルタイムダッシュボード（経過時間・進捗表示付き）"""
    from .storage.task_store import TaskStore
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    from datetime import datetime
    import time
    import os

    store = TaskStore()
    console = Console()

    # スピナーのフレーム
    spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    frame_idx = [0]  # ミュータブルなカウンター

    def truncate(text: str, max_len: int = 35) -> str:
        """説明文を短く切り詰める（最初の行のみ）"""
        first_line = text.split('\n')[0].strip()
        if len(first_line) > max_len:
            return first_line[:max_len] + "..."
        return first_line

    def format_duration(start_str: str, end_str: str = None) -> str:
        """経過時間をフォーマット"""
        if not start_str:
            return "-"
        try:
            start = datetime.fromisoformat(start_str)
            end = datetime.fromisoformat(end_str) if end_str else datetime.now()
            delta = end - start
            total_seconds = int(delta.total_seconds())

            if total_seconds < 60:
                return f"{total_seconds}s"
            elif total_seconds < 3600:
                mins = total_seconds // 60
                secs = total_seconds % 60
                return f"{mins}m {secs}s"
            else:
                hours = total_seconds // 3600
                mins = (total_seconds % 3600) // 60
                return f"{hours}h {mins}m"
        except Exception:
            return "-"

    def is_process_running(pid: int) -> bool:
        """プロセスが実行中かチェック"""
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def generate_display():
        """テーブルとサマリーを生成"""
        tasks = store.list_tasks(limit=15)

        # サマリー計算
        running = sum(1 for t in tasks if t["status"] == "running")
        completed = sum(1 for t in tasks if t["status"] == "completed")
        failed = sum(1 for t in tasks if t["status"] == "failed")
        pending = sum(1 for t in tasks if t["status"] == "pending")

        # スピナーフレーム更新
        spinner = spinner_frames[frame_idx[0] % len(spinner_frames)]
        frame_idx[0] += 1

        # ヘッダーサマリー
        now = datetime.now().strftime("%H:%M:%S")
        header = Text()
        header.append(f"🕐 {now}  ", style="dim")
        if running > 0:
            header.append(f"{spinner} ", style="yellow bold")
        header.append(f"🔄 Running: {running}  ", style="yellow")
        header.append(f"✅ Done: {completed}  ", style="green")
        if failed > 0:
            header.append(f"❌ Failed: {failed}  ", style="red")
        if pending > 0:
            header.append(f"⏳ Pending: {pending}", style="dim")

        # テーブル
        table = Table(title="", box=None, padding=(0, 1))
        table.add_column("", width=2)  # アイコン
        table.add_column("ID", style="cyan", no_wrap=True, width=10)
        table.add_column("Profile", no_wrap=True, width=8)
        table.add_column("Status", width=12)
        table.add_column("Duration", width=10, justify="right")
        table.add_column("Description", max_width=40, no_wrap=True)

        for t in tasks:
            status = t["status"]
            pid = t.get("pid")

            # アイコンと色
            if status == "running":
                icon = spinner
                color = "yellow"
                # プロセスが実際に動いているかチェック
                if pid and not is_process_running(pid):
                    icon = "⚠️"
                    color = "red"
            elif status == "completed":
                icon = "✅"
                color = "green"
            elif status == "failed":
                icon = "❌"
                color = "red"
            elif status == "pending":
                icon = "⏳"
                color = "dim"
            elif status == "cancelled":
                icon = "🚫"
                color = "dim"
            else:
                icon = "❓"
                color = "white"

            # 経過時間
            if status == "running":
                duration = format_duration(t["started_at"])
            elif status in ("completed", "failed"):
                duration = format_duration(t["started_at"], t["completed_at"])
            else:
                duration = "-"

            # ステータス表示（進捗詳細付き）
            status_text = status
            if status == "running":
                # 進捗詳細を取得
                from .core.task_runner import TaskRunner
                runner = TaskRunner()
                action = runner.get_last_action(t["task_id"])
                if action:
                    status_text = f"{status} ({action})"
                elif pid:
                    status_text = f"{status} ({pid})"

            table.add_row(
                icon,
                t["task_id"][:10],
                t["profile"][:8] if t["profile"] else "-",
                f"[{color}]{status_text}[/]",
                f"[{color}]{duration}[/]",
                truncate(t["task_description"])
            )

        # パネルにまとめる
        from rich.console import Group
        return Panel(
            Group(header, "", table),
            title="[bold cyan]🚀 Moco Task Dashboard[/]",
            subtitle="[dim]Press Ctrl+C to exit[/]",
            border_style="cyan"
        )

    try:
        with Live(generate_display(), refresh_per_second=2, console=console) as live:
            while True:
                time.sleep(0.5)
                live.update(generate_display())
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard closed.[/]")


@tasks_app.command("logs")
def tasks_logs(
    task_id: str = typer.Argument(..., help="タスクID"),
    follow: bool = typer.Option(False, "--follow", "-f", help="ログを継続的に表示"),
    all_logs: bool = typer.Option(False, "--all", "-a", help="全ログを表示（切り詰めなし）"),
):
    """タスクのログを表示"""
    from .core.task_runner import TaskRunner
    runner = TaskRunner()
    if follow:
        runner.tail_logs(task_id)
    else:
        max_bytes = 0 if all_logs else 10000  # 0 = 無制限
        logs = runner.get_logs(task_id, max_bytes=max_bytes)
        typer.echo(logs)


@tasks_app.command("cancel")
def tasks_cancel(
    task_id: str = typer.Argument(..., help="タスクID"),
):
    """実行中のタスクをキャンセル"""
    from .core.task_runner import TaskRunner
    runner = TaskRunner()
    if runner.cancel_task(task_id):
        typer.echo(f"Task {task_id} cancelled.")
    else:
        typer.echo(f"Failed to cancel task {task_id}.")


@tasks_app.command("_exec", hidden=True)
def tasks_exec(
    task_id: str,
    profile: str,
    task_description: str,
    provider: Optional[str] = typer.Option(None, "--provider", help="プロバイダ"),
    model: Optional[str] = typer.Option(None, "--model", help="モデル名"),
    working_dir: Optional[str] = typer.Option(None, "--working-dir", help="作業ディレクトリ"),
    session: Optional[str] = typer.Option(None, "--session", help="継続するセッションID"),
):
    """(内部用) タスクを実行し、DBを更新する"""
    init_environment()
    from .storage.task_store import TaskStore, TaskStatus
    from .core.llm_provider import get_available_provider

    # 作業ディレクトリを環境変数に設定
    if working_dir:
        os.environ['MOCO_WORKING_DIRECTORY'] = working_dir

    store = TaskStore()

    # プロバイダの解決
    if provider is None:
        provider = get_available_provider()
    
    provider_enum, model = resolve_provider(provider, model)

    try:
        from .core.orchestrator import Orchestrator
        orchestrator = Orchestrator(profile=profile, provider=provider_enum, model=model, working_directory=working_dir)
        
        # セッションIDが指定されている場合は継続、なければ新規作成
        if session:
            orchestrator.session_id = session
        else:
            orchestrator.create_session(title=f"Task: {task_description[:50]}")
        
        # run_sync を使用してタスクを実行
        result = orchestrator.run_sync(task_description)

        store.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            result=result,
            completed_at=datetime.now().isoformat()
        )
    except Exception as e:
        print(f"Error in background task {task_id}: {e}", file=sys.stderr)
        store.update_task(
            task_id,
            status=TaskStatus.FAILED,
            error=str(e),
            completed_at=datetime.now().isoformat()
        )


@app.command()
def ui(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="ホストアドレス"),
    port: int = typer.Option(8000, "--port", "-p", help="ポート番号"),
    reload: bool = typer.Option(False, "--reload", "-r", help="開発モード（自動リロード）"),
):
    """Web UI を起動"""
    import uvicorn
    from rich.console import Console
    
    console = Console()
    console.print("\n🚀 [bold cyan]Moco Web UI[/bold cyan] starting...")
    console.print(f"   URL: [link]http://{host if host != '0.0.0.0' else 'localhost'}:{port}[/link]\n")
    
    uvicorn.run(
        "moco.ui.api:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info"
    )


def main():
    app()


if __name__ == "__main__":
    main()
