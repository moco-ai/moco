import shlex
from typing import List, Dict, Any, Optional
from rich.console import Console
from rich.table import Table
import typer
from .ui.theme import THEMES, ThemeName
from .ui.layout import ui_state

def handle_slash_command(text: str, context: Dict[str, Any]) -> bool:
    """スラッシュコマンドを処理。True を返すとチャット継続、False で終了"""
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
        
    if not parts:
        return True
    
    command = parts[0].lower().lstrip('/')
    args = parts[1:]
    
    SLASH_COMMANDS = {
        'help': handle_help,
        'clear': handle_clear,
        'model': handle_model,
        'profile': handle_profile,
        'session': handle_session,
        'save': handle_save,
        'cost': handle_cost,
        'tools': handle_tools,
        'agents': handle_agents,
        'quit': handle_quit,
        'exit': handle_quit,
        'theme': handle_theme,
    }
    
    if command in SLASH_COMMANDS:
        return SLASH_COMMANDS[command](args, context)
    else:
        # カスタムコマンドが存在するかチェック（tools/command_loaderがあれば）
        try:
            from .tools.command_loader import load_custom_commands, render_command, parse_command_args
            custom_commands = load_custom_commands()
            if command in custom_commands:
                custom_cmd = custom_commands[command]
                console = context.get('console', Console())
                arg_dict = parse_command_args(custom_cmd.arguments, args)
                missing = [a.get('name') for a in custom_cmd.arguments if a.get('required') and not arg_dict.get(a.get('name'))]
                if missing:
                    console.print(f"[red]Error: Missing required arguments: {', '.join(missing)}[/red]")
                    console.print(f"[dim]Usage: /{command} {' '.join(['<' + a.get('name') + '>' for a in custom_cmd.arguments])}[/dim]")
                    return True
                prompt = render_command(custom_cmd, arg_dict)
                context['pending_prompt'] = prompt
                return True
        except ImportError:
            pass

        console = context.get('console', Console())
        console.print(f"[red]Unknown command: /{command}. Type /help for available commands.[/red]")
        return True

def handle_help(args: List[str], context: Dict[str, Any]) -> bool:
    """利用可能なコマンド一覧を表示"""
    console = context.get('console', Console())
    table = Table(title="Available Commands", border_style="cyan")
    table.add_column("Command", style="cyan")
    table.add_column("Description")
    
    commands = [
        ("/help", "Show this help message"),
        ("/clear", "Clear conversation history"),
        ("/model [name]", "Show or change current model"),
        ("/profile [name]", "Show or change current profile"),
        ("/theme [name]", "Show or change current theme"),
        ("/session", "Show current session info"),
        ("/save", "Save current session (Automatic)"),
        ("/cost", "Show estimated cost for this session"),
        ("/tools", "List available tools"),
        ("/agents", "List available agents"),
        ("/quit", "Exit chat"),
    ]
    for cmd, desc in commands:
        table.add_row(cmd, desc)
    
    console.print(table)

    # カスタムコマンドを表示
    try:
        from .tools.command_loader import load_custom_commands
        custom_commands = load_custom_commands()
        if custom_commands:
            console.print("\n[bold]Custom Commands:[/bold]")
            custom_table = Table(border_style="cyan")
            custom_table.add_column("Command", style="cyan")
            custom_table.add_column("Description")
            for name, cmd in custom_commands.items():
                custom_table.add_row(f"/{name}", cmd.description)
            console.print(custom_table)
    except ImportError:
        pass

    return True

def handle_clear(args: List[str], context: Dict[str, Any]) -> bool:
    """会話履歴をクリア"""
    orchestrator = context.get('orchestrator')
    session_id = context.get('session_id')
    console = context.get('console', Console())
    
    if orchestrator and session_id:
        from .core.orchestrator import Orchestrator
        new_session_id = orchestrator.create_session(title="CLI Chat (Cleared)")
        context['session_id'] = new_session_id
        console.print("[green]✓ Conversation history cleared (New session started).[/green]")
    return True

def handle_quit(args: List[str], context: Dict[str, Any]) -> bool:
    """チャットを終了"""
    console = context.get('console', Console())
    console.print("[dim]Goodbye![/dim]")
    return False

def handle_theme(args: List[str], context: Dict[str, Any]) -> bool:
    """テーマを表示・変更"""
    console = context.get('console', Console())
    if not args:
        available = ", ".join([t.value for t in ThemeName])
        console.print(f"[dim]Available themes: {available}[/dim]")
        console.print(f"[dim]Current theme: {ui_state.theme.value}[/dim]")
        console.print(f"[dim]Usage: /theme <name>[/dim]")
    else:
        new_theme_str = args[0].lower()
        try:
            new_theme = ThemeName(new_theme_str)
            ui_state.theme = new_theme
            console.print(f"[green]Theme changed to: {new_theme.value}[/green]")
        except ValueError:
            console.print(f"[red]Error: Unknown theme '{new_theme_str}'[/red]")
    return True

def handle_model(args: List[str], context: Dict[str, Any]) -> bool:
    """モデルを表示・変更"""
    console = context.get('console', Console())
    orchestrator = context.get('orchestrator')
    if not orchestrator:
        return True

    if not args:
        current_model = getattr(orchestrator, 'model', 'default')
        console.print(f"[dim]Current model: {current_model}[/dim]")
        console.print(f"[dim]Usage: /model <model_name>[/dim]")
    else:
        new_model = args[0]
        orchestrator.model = new_model
        # 各エージェントのランタイムにも反映させる必要がある
        for runtime in orchestrator.runtimes.values():
            if hasattr(runtime, 'model_name'):
                runtime.model_name = new_model
            else:
                runtime.model = new_model
        console.print(f"[green]Model changed to: {new_model}[/green]")
    return True

def handle_profile(args: List[str], context: Dict[str, Any]) -> bool:
    """プロファイルを表示・変更"""
    console = context.get('console', Console())
    orchestrator = context.get('orchestrator')
    if not orchestrator:
        return True

    if not args:
        console.print(f"[dim]Current profile: {orchestrator.profile}[/dim]")
        console.print(f"[dim]Usage: /profile <profile_name>[/dim]")
    else:
        new_profile = args[0]
        # プロファイルの変更は Orchestrator の再初期化が必要
        console.print(f"[dim]Changing profile to '{new_profile}'...[/dim]")
        orchestrator.profile = new_profile
        orchestrator.loader.profile = new_profile
        orchestrator.skill_loader.profile = new_profile
        if hasattr(orchestrator, 'optimizer_config'):
            orchestrator.optimizer_config.profile = new_profile
        orchestrator.reload_agents()
        console.print(f"[green]Profile changed to: {new_profile}[/green]")
    return True

def handle_session(args: List[str], context: Dict[str, Any]) -> bool:
    """セッション情報を表示"""
    console = context.get('console', Console())
    session_id = context.get('session_id')
    orchestrator = context.get('orchestrator')
    
    if orchestrator:
        table = Table(title="Session Information", border_style="cyan")
        table.add_column("Property", style="cyan")
        table.add_column("Value")
        table.add_row("Session ID", session_id)
        table.add_row("Profile", orchestrator.profile)
        table.add_row("Model", str(getattr(orchestrator, 'model', 'default')))
        
        provider_name = "unknown"
        if hasattr(orchestrator, 'provider'):
            p = orchestrator.provider
            provider_name = getattr(p, 'name', getattr(p, 'value', str(p)))
        
        table.add_row("Provider", provider_name)
        console.print(table)
    elif session_id:
        console.print(f"[dim]Current Session ID: {session_id}[/dim]")
    return True

def handle_save(args: List[str], context: Dict[str, Any]) -> bool:
    """現在のセッションを保存（Mocoでは自動保存されるため、通知のみ）"""
    console = console = context.get('console', Console())
    console.print("[green]Session is automatically saved.[/green]")
    return True

def handle_cost(args: List[str], context: Dict[str, Any]) -> bool:
    """コスト表示（未実装）"""
    console = context.get('console', Console())
    console.print("[yellow]/cost is not yet implemented.[/yellow]")
    return True

def handle_tools(args: List[str], context: Dict[str, Any]) -> bool:
    """利用可能なツール一覧を表示"""
    console = context.get('console', Console())
    orchestrator = context.get('orchestrator')
    if not orchestrator:
        return True

    table = Table(title="Available Tools", border_style="green")
    table.add_column("Tool Name", style="cyan")
    
    if hasattr(orchestrator, 'tool_map'):
        for tool_name in sorted(orchestrator.tool_map.keys()):
            table.add_row(tool_name)
    
    console.print(table)
    return True

def handle_agents(args: List[str], context: Dict[str, Any]) -> bool:
    """利用可能なエージェント一覧を表示"""
    console = context.get('console', Console())
    orchestrator = context.get('orchestrator')
    if not orchestrator:
        return True

    table = Table(title="Available Agents", border_style="magenta")
    table.add_column("Agent Name", style="cyan")
    table.add_column("Description")
    
    if hasattr(orchestrator, 'agents'):
        for name, config in orchestrator.agents.items():
            desc = config.instructions[:50] + "..." if config.instructions else ""
            table.add_row(name, desc)
    
    console.print(table)
    return True
