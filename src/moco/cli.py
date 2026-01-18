#!/usr/bin/env python3
"""Moco CLI"""

import os
import typer
import time
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from dotenv import load_dotenv, find_dotenv
from .ui.theme import ThemeName, THEMES

def init_environment():
    """環境変数の初期化"""
    # 1. find_dotenv() で自動検索（カレントディレクトリから親方向に .env を探索）
    # 2. フォールバックとして従来のパス（__file__ 基準で3階層上）
    env_path = find_dotenv() or (Path(__file__).parent.parent.parent / ".env")
    if env_path:
        load_dotenv(env_path)


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


@app.command()
def run(
    task: str = typer.Argument(..., help="実行するタスク"),
    profile: str = typer.Option("default", "--profile", "-p", help="使用するプロファイル"),
    provider: Optional[str] = typer.Option(None, "--provider", help="LLMプロバイダ (gemini/openai/openrouter/zai) - 省略時は自動選択"),
    stream: bool = typer.Option(False, "--stream/--no-stream", help="ストリーミング出力（デフォルト: オフ）"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="詳細ログ"),
    rich_output: bool = typer.Option(True, "--rich/--plain", help="リッチ出力"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="セッション名（継続 or 新規）"),
    cont: bool = typer.Option(False, "--continue", "-c", help="直前のセッションを継続"),
    auto_retry: int = typer.Option(0, "--auto-retry", help="エラー時の自動リトライ回数"),
    retry_delay: int = typer.Option(3, "--retry-delay", help="リトライ間隔（秒）"),
    show_metrics: bool = typer.Option(False, "--show-metrics", "-m", help="メトリクス表示"),
    theme: ThemeName = typer.Option(ThemeName.DEFAULT, "--theme", help="UIカラーテーマ", case_sensitive=False),
    use_optimizer: bool = typer.Option(True, "--optimizer/--no-optimizer", help="Optimizerによるエージェント自動選択"),
    working_dir: Optional[str] = typer.Option(None, "--working-dir", "-w", help="作業ディレクトリ（subagentに自動伝達）"),
    sandbox: bool = typer.Option(False, "--sandbox", help="Dockerコンテナ内でコマンドを実行"),
    sandbox_image: str = typer.Option("python:3.12-slim", "--sandbox-image", help="サンドボックスで使用するDockerイメージ"),
):
    """タスクを実行"""
    if session and cont:
        typer.echo("Error: --session と --continue は同時に指定できません。", err=True)
        raise typer.Exit(code=1)

    if sandbox:
        os.environ["MOCO_SANDBOX"] = "1"
        os.environ["MOCO_SANDBOX_IMAGE"] = sandbox_image

    from .ui.layout import ui_state
    ui_state.theme = theme

    theme_config = THEMES[theme]

    init_environment()

    # 作業ディレクトリを環境変数に設定（ツールから参照可能にする）
    # 注意: os.chdir() はプロファイル読み込みに影響するため、ここでは行わない
    # ツール側で MOCO_WORKING_DIRECTORY を使って絶対パスに変換する
    original_cwd = os.getcwd()
    if working_dir:
        os.environ['MOCO_WORKING_DIRECTORY'] = os.path.abspath(working_dir)

    from .core.orchestrator import Orchestrator
    from .core.runtime import LLMProvider
    from .core.llm_provider import get_available_provider

    # プロバイダーの解決（指定なしの場合は優先順位で自動選択）
    if provider is None:
        provider = get_available_provider()

    if provider == "openai":
        provider_enum = LLMProvider.OPENAI
    elif provider == "openrouter":
        provider_enum = LLMProvider.OPENROUTER
    elif provider == "zai":
        provider_enum = LLMProvider.ZAI
    else:
        provider_enum = LLMProvider.GEMINI

    if rich_output:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()

    o = Orchestrator(
        profile=profile,
        provider=provider_enum,
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
    last_error = None

    # キャンセルイベントの作成
    from .cancellation import create_cancel_event, request_cancel, clear_cancel_event, OperationCancelled
    cancel_event = create_cancel_event(session_id)

    def listen_for_cancel():
        """キー入力を監視してキャンセルをリクエストする"""
        if sys.platform == "win32":
            import msvcrt
            while not cancel_event.is_set():
                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    if ch in (b'\x1b', b'\x03'):  # ESC or Ctrl+C
                        request_cancel(session_id)
                        break
                time.sleep(0.1)
        else:
            import tty
            import termios
            import select
            fd = sys.stdin.fileno()
            if not os.isatty(fd):
                return
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                while not cancel_event.is_set():
                    # select で入力を待機 (100ms)
                    rlist, _, _ = select.select([fd], [], [], 0.1)
                    if rlist:
                        ch = sys.stdin.read(1)
                        if ch in ('\x1b', '\x03'):  # ESC or Ctrl+C
                            request_cancel(session_id)
                            break
                        # 注意: ツール等がユーザー入力を求めた場合、ここで読み取った文字は消失する
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    cancel_thread = threading.Thread(target=listen_for_cancel, daemon=True)
    cancel_thread.start()

    try:
        for attempt in range(auto_retry + 1):
            try:
                result = o.run_sync(task, session_id)
                break
            except OperationCancelled:
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
        # スレッドに停止を通知
        cancel_event.set()
        if sys.platform != "win32":
            # 端末プロパティ復元を確実にするため、スレッドの終了を待つ
            cancel_thread.join(timeout=0.2)
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
    """結果を整形して表示

    Args:
        console: Rich console
        result: 結果文字列
        verbose: True なら全エージェント出力を表示、False なら最後だけ
    """
    from rich.panel import Panel
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
                    console.print(Panel(
                        content,
                        title=f"[bold {theme.thoughts}]{agent}[/]",
                        border_style="dim",
                    ))
                i += 2
        else:
            # 最後のエージェントの結果だけ表示
            last_agent = sections[-2] if len(sections) >= 2 else ""
            last_content = sections[-1].strip() if sections[-1] else ""

            # orchestrator の最終回答は省略しない、他は短縮
            if last_agent == "@orchestrator":
                # 最終回答は全文表示
                display = last_content
            else:
                # 中間エージェントは省略可
                lines = last_content.split('\n')
                if len(lines) > 20:
                    display = '\n'.join(lines[:20]) + f"\n\n[dim]... ({len(lines) - 20} lines omitted, use -v for full output)[/dim]"
                else:
                    display = last_content

            console.print(Panel(
                display,
                title=f"[bold {theme.thoughts}]{last_agent}[/]",
                border_style="dim" if last_agent != "@orchestrator" else theme.result,
            ))

    # 最終サマリーを表示
    if final_summary:
        console.print(Panel(
            final_summary,
            title=f"[bold {theme.result}]✅ まとめ[/]",
            border_style=theme.result,
        ))
    elif len(sections) > 1:
        console.print(f"\n[bold {theme.result}]✅ 完了[/]")
    else:
        # 単一の応答
        console.print(Panel(
            result,
            title="📋 Result",
            border_style=theme.result,
        ))


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
    profile: str = typer.Option("default", "--profile", "-p", help="使用するプロファイル"),
    provider: Optional[str] = typer.Option(None, "--provider", help="LLMプロバイダ (gemini/openai/openrouter/zai) - 省略時は自動選択"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="詳細ログ"),
    theme: ThemeName = typer.Option(ThemeName.DEFAULT, "--theme", help="UIカラーテーマ", case_sensitive=False),
    use_optimizer: bool = typer.Option(True, "--optimizer/--no-optimizer", help="Optimizerによるエージェント自動選択"),
    sandbox: bool = typer.Option(False, "--sandbox", help="Dockerコンテナ内でコマンドを実行"),
    sandbox_image: str = typer.Option("python:3.12-slim", "--sandbox-image", help="サンドボックスで使用するDockerイメージ"),
):
    """対話型チャット"""
    from .ui.layout import ui_state
    ui_state.theme = theme
    theme_config = THEMES[theme]

    if sandbox:
        os.environ["MOCO_SANDBOX"] = "1"
        os.environ["MOCO_SANDBOX_IMAGE"] = sandbox_image

    init_environment()
    from rich.console import Console
    from rich.panel import Panel


    from .core.orchestrator import Orchestrator
    from .core.runtime import LLMProvider
    from .core.llm_provider import get_available_provider

    console = Console()

    # プロバイダーの解決（指定なしの場合は優先順位で自動選択）
    if provider is None:
        provider = get_available_provider()

    if provider == "openai":
        provider_enum = LLMProvider.OPENAI
    elif provider == "openrouter":
        provider_enum = LLMProvider.OPENROUTER
    elif provider == "zai":
        provider_enum = LLMProvider.ZAI
    else:
        provider_enum = LLMProvider.GEMINI

    o = Orchestrator(
        profile=profile,
        provider=provider_enum,
        stream=False,
        verbose=verbose,
        use_optimizer=use_optimizer,
    )

    # Context for slash commands
    command_context = {
        'orchestrator': o,
        'console': console,
        'verbose': verbose,
    }

    # セッション自動継続または新規作成
    sessions = o.session_logger.list_sessions(limit=1)
    if sessions:
        session_id = sessions[0].get("session_id")
        console.print(f"[dim]Using session: {session_id[:8]}...[/dim]")
    else:
        session_id = o.create_session(title="CLI Chat")
        console.print(f"[dim]New session: {session_id[:8]}...[/dim]")

    command_context['session_id'] = session_id

    console.print(Panel(
        f"[bold {theme_config.status}]Profile:[/] {profile}  [bold {theme_config.status}]Provider:[/] {provider}\n"
        f"[dim]Type 'exit' to quit, '/help' for commands[/dim]",
        title="🤖 Moco chat",
        border_style=theme_config.tools
    ))

    # --- スラッシュコマンド対応 ---
    from .cli_commands import handle_slash_command
    from .cancellation import create_cancel_event, request_cancel, clear_cancel_event, OperationCancelled
    # ---

    try:
        while True:
            # 最新のテーマ設定を反映
            theme_config = THEMES[ui_state.theme]

            try:
                text = console.input(f"[bold {theme_config.status}]> [/bold {theme_config.status}]")
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
                cancel_event = create_cancel_event(session_id)
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

            if reply:
                console.print()
                _print_result(console, reply, theme_name=ui_state.theme, verbose=verbose)
                console.print()
    except KeyboardInterrupt:
        console.print("\n[dim]Bye![/dim]")


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
        console.print(f"[dim]Try: moco skills sync anthropics[/dim]")
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
def version():
    """バージョン表示"""
    typer.echo("Moco v0.1.0")


# --- Tasks Subcommands ---

@tasks_app.command("run")
def tasks_run(
    task: str = typer.Argument(..., help="実行するタスク内容"),
    profile: str = typer.Option("default", "--profile", "-p", help="プロファイル"),
    provider: Optional[str] = typer.Option(None, "--provider", help="プロバイダ - 省略時は自動選択"),
    working_dir: Optional[str] = typer.Option(None, "--working-dir", "-w", help="作業ディレクトリ"),
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

    # 作業ディレクトリを絶対パスに解決
    resolved_working_dir = None
    if working_dir:
        resolved_working_dir = os.path.abspath(working_dir)

    store = TaskStore()
    task_id = store.add_task(task, profile, provider, resolved_working_dir)

    runner = TaskRunner(store)
    runner.run_task(task_id, profile, task, resolved_working_dir, provider)

    typer.echo(f"Task started: {task_id}")


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
        except:
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
        except:
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
):
    """タスクのログを表示"""
    from .core.task_runner import TaskRunner
    runner = TaskRunner()
    if follow:
        runner.tail_logs(task_id)
    else:
        logs = runner.get_logs(task_id)
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
    working_dir: Optional[str] = typer.Option(None, "--working-dir", help="作業ディレクトリ"),
):
    """(内部用) タスクを実行し、DBを更新する"""
    init_environment()
    from .storage.task_store import TaskStore, TaskStatus

    # 作業ディレクトリを環境変数に設定
    if working_dir:
        os.environ['MOCO_WORKING_DIRECTORY'] = working_dir

    store = TaskStore()

    # プロバイダの解決
    from .core.runtime import LLMProvider
    from .core.llm_provider import get_available_provider
    
    if provider is None:
        provider = get_available_provider()
    
    p_enum = provider  # 文字列をそのまま渡す
    if provider == "openai": p_enum = LLMProvider.OPENAI
    elif provider == "gemini": p_enum = LLMProvider.GEMINI
    elif provider == "zai": p_enum = LLMProvider.ZAI
    elif provider == "openrouter": p_enum = LLMProvider.OPENROUTER

    try:
        from .core.orchestrator import Orchestrator
        orchestrator = Orchestrator(profile=profile, provider=p_enum, working_directory=working_dir)
        # run_sync を使用してタスクを実行
        result = orchestrator.run_sync(task_description)

        store.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            result=result,
            completed_at=datetime.now().isoformat()
        )
    except Exception as e:
        store.update_task(
            task_id,
            status=TaskStatus.FAILED,
            error=str(e),
            completed_at=datetime.now().isoformat()
        )


def main():
    app()


if __name__ == "__main__":
    main()
