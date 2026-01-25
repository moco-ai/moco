import shlex
from typing import List, Dict, Any
from rich.console import Console
from rich.table import Table
from .ui.theme import ThemeName
from .ui.layout import ui_state


def _print_out(context: Dict[str, Any], message: str) -> None:
    """Print message respecting async-input plain output mode."""
    if context.get("plain_output") and callable(context.get("plain_print")):
        context["plain_print"](str(message))
        return
    console = context.get("console", Console())
    console.print(message)


def handle_slash_command(text: str, context: Dict[str, Any]) -> bool:
    """Process slash commands. Returns True to continue chat, False to exit."""
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
        'todo': handle_todo,
        'substream': handle_substream,
        'toolstatus': handle_toolstatus,
        'quit': handle_quit,
        'exit': handle_quit,
        'theme': handle_theme,
        'workdir': handle_workdir,
        'cd': handle_workdir,
        'ls': handle_ls,
        'tree': handle_tree,
    }
    
    if command in SLASH_COMMANDS:
        return SLASH_COMMANDS[command](args, context)
    else:
        # Check if custom command exists (if tools/command_loader exists)
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

        # Plain output in async-input mode to avoid raw ANSI escapes.
        if context.get("plain_output"):
            _print_out(context, f"Unknown command: /{command}. Type /help for available commands.")
        else:
            _print_out(context, f"[red]Unknown command: /{command}. Type /help for available commands.[/red]")
        return True


def handle_todo(args: List[str], context: Dict[str, Any]) -> bool:
    """Show current todo list for this chat session.

    Usage:
      /todo          -> show hierarchical todos (orchestrator + subagents)
      /todo refresh  -> refresh todo pane (when --todo-pane is enabled)
    """
    console = context.get('console', Console())
    session_id = context.get("session_id")

    pane_enabled = bool(context.get("pane_enabled"))
    pane_append = context.get("pane_append")
    pane_refresh_chat = context.get("pane_refresh_chat")
    pane_refresh_todo = context.get("pane_refresh_todo")

    # Refresh-only mode (useful when todo-pane is enabled)
    if args and args[0].lower() in ("refresh", "r"):
        if pane_enabled and callable(pane_refresh_todo):
            pane_refresh_todo()
            if callable(pane_append) and callable(pane_refresh_chat):
                pane_append("[dim]✓ Todos refreshed[/dim]")
                pane_refresh_chat()
        else:
            console.print("[dim](todo pane is not enabled)[/dim]")
        return True

    try:
        from moco.tools.todo import set_current_session, todoread_all
        if session_id:
            set_current_session(session_id)
        out = todoread_all()
    except Exception as e:
        out = f"Error: failed to read todos: {e}"

    if pane_enabled and callable(pane_append) and callable(pane_refresh_chat):
        pane_append("[bold]Todos[/bold]")
        for ln in (out or "").splitlines():
            pane_append(ln)
        pane_refresh_chat()
        if callable(pane_refresh_todo):
            pane_refresh_todo()
        return True

    console.print(out)
    return True

def handle_toolstatus(args: List[str], context: Dict[str, Any]) -> bool:
    """Toggle tool/delegation status lines in CLI.

    Usage:
      /toolstatus            -> show current state
      /toolstatus on|off     -> enable/disable
    """
    console = context.get('console', Console())
    flags = context.get('stream_flags') or {}
    current = bool(flags.get("show_tool_status", True))

    if not args:
        if context.get("plain_output"):
            _print_out(context, f"Tool status display: {'ON' if current else 'OFF'}")
            _print_out(context, "Usage: /toolstatus on|off")
        else:
            console.print(f"[dim]Tool status display:[/dim] {'ON' if current else 'OFF'}")
            console.print("[dim]Usage: /toolstatus on|off[/dim]")
        return True

    val = args[0].lower()
    if val in ("on", "true", "1", "yes"):
        flags["show_tool_status"] = True
        _print_out(context, "Tool status display: ON" if context.get("plain_output") else "[green]Tool status display: ON[/green]")
        return True
    if val in ("off", "false", "0", "no"):
        flags["show_tool_status"] = False
        _print_out(context, "Tool status display: OFF" if context.get("plain_output") else "[green]Tool status display: OFF[/green]")
        return True

    _print_out(context, "Usage: /toolstatus on|off" if context.get("plain_output") else "[red]Usage: /toolstatus on|off[/red]")
    return True

def handle_substream(args: List[str], context: Dict[str, Any]) -> bool:
    """Toggle sub-agent streamed text in CLI.

    Usage:
      /substream            -> show current state
      /substream on|off     -> enable/disable
    """
    console = context.get('console', Console())
    flags = context.get('stream_flags') or {}
    current = bool(flags.get("show_subagent_stream", False))

    if not args:
        if context.get("plain_output"):
            _print_out(context, f"Sub-agent stream: {'ON' if current else 'OFF'}")
            _print_out(context, "Usage: /substream on|off")
        else:
            console.print(f"[dim]Sub-agent stream:[/dim] {'ON' if current else 'OFF'}")
            console.print("[dim]Usage: /substream on|off[/dim]")
        return True

    val = args[0].lower()
    if val in ("on", "true", "1", "yes"):
        flags["show_subagent_stream"] = True
        _print_out(context, "Sub-agent stream: ON" if context.get("plain_output") else "[green]Sub-agent stream: ON[/green]")
        return True
    if val in ("off", "false", "0", "no"):
        flags["show_subagent_stream"] = False
        _print_out(context, "Sub-agent stream: OFF" if context.get("plain_output") else "[green]Sub-agent stream: OFF[/green]")
        return True

    _print_out(context, "Usage: /substream on|off" if context.get("plain_output") else "[red]Usage: /substream on|off[/red]")
    return True

def handle_ls(args: List[str], context: Dict[str, Any]) -> bool:
    """Display contents of current directory"""
    import os
    from rich.table import Table
    console = context.get('console', Console())
    orchestrator = context.get('orchestrator')
    
    path = args[0] if args else (orchestrator.working_directory if orchestrator else os.getcwd())
    
    try:
        items = os.listdir(path)
        table = Table(title=f"Contents of {path}", border_style="dim")
        table.add_column("Type", width=6)
        table.add_column("Name")
        
        # Prioritize directories
        for item in sorted(items):
            full_path = os.path.join(path, item)
            is_dir = os.path.isdir(full_path)
            if is_dir:
                table.add_row("[bold cyan]DIR[/]", item)
        
        for item in sorted(items):
            full_path = os.path.join(path, item)
            if not os.path.isdir(full_path):
                table.add_row("FILE", item)
                
        console.print(table)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    return True

def handle_tree(args: List[str], context: Dict[str, Any]) -> bool:
    """Tree display of directory structure"""
    import os
    from rich.tree import Tree
    console = context.get('console', Console())
    orchestrator = context.get('orchestrator')
    
    root_path = orchestrator.working_directory if orchestrator else os.getcwd()
    max_depth = int(args[0]) if args and args[0].isdigit() else 2
    
    def add_tree_node(node, current_path, current_depth):
        if current_depth > max_depth:
            return
        try:
            items = sorted(os.listdir(current_path))
            for item in items:
                if item.startswith('.') and item not in ('.moco', '.env'):
                    continue
                full_path = os.path.join(current_path, item)
                if os.path.isdir(full_path):
                    branch = node.add(f"[bold cyan]{item}/[/]")
                    add_tree_node(branch, full_path, current_depth + 1)
                else:
                    node.add(item)
        except PermissionError:
            pass

    tree = Tree(f"[bold]{root_path}[/]")
    add_tree_node(tree, root_path, 1)
    console.print(tree)
    return True

def handle_help(args: List[str], context: Dict[str, Any]) -> bool:
    """Display available commands"""
    console = context.get('console', Console())
    if context.get("plain_output"):
        _print_out(context, "Available Commands:")
        _print_out(context, "  /help              Show this help message")
        _print_out(context, "  /clear             Clear conversation history")
        _print_out(context, "  /model [name]      Show or change current model")
        _print_out(context, "  /profile [name]    Show or change current profile")
        _print_out(context, "  /theme [name]      Show or change current theme")
        _print_out(context, "  /workdir [path]    Show or change working directory")
        _print_out(context, "  /ls [path]         List directory contents")
        _print_out(context, "  /tree [depth]      Show directory tree (default depth 2)")
        _print_out(context, "  /session           Show current session info")
        _print_out(context, "  /save              Save current session (Automatic)")
        _print_out(context, "  /cost              Show estimated cost for this session")
        _print_out(context, "  /tools             List available tools")
        _print_out(context, "  /agents            List available agents")
        _print_out(context, "  /todo              Show current Todo list for this session")
        _print_out(context, "  /toolstatus on|off Toggle tool/delegation status lines")
        _print_out(context, "  /quit              Exit chat")
        return True
    table = Table(title="Available Commands", border_style="cyan")
    table.add_column("Command", style="cyan")
    table.add_column("Description")
    
    commands = [
        ("/help", "Show this help message"),
        ("/clear", "Clear conversation history"),
        ("/model [name]", "Show or change current model"),
        ("/profile [name]", "Show or change current profile"),
        ("/theme [name]", "Show or change current theme"),
        ("/workdir [path]", "Show or change working directory"),
        ("/cd [path]", "Alias for /workdir"),
        ("/ls [path]", "List directory contents"),
        ("/tree [depth]", "Show directory tree (default depth 2)"),
        ("/session", "Show current session info"),
        ("/save", "Save current session (Automatic)"),
        ("/cost", "Show estimated cost for this session"),
        ("/tools", "List available tools"),
        ("/agents", "List available agents"),
        ("/todo", "Show current Todo list for this session"),
        ("/toolstatus on|off", "Toggle tool/delegation status lines"),
        ("/quit", "Exit chat"),
    ]
    for cmd, desc in commands:
        table.add_row(cmd, desc)
    
    console.print(table)

    # Display custom commands
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
    """Clear conversation history"""
    orchestrator = context.get('orchestrator')
    session_id = context.get('session_id')
    console = context.get('console', Console())
    
    if orchestrator and session_id:
        new_session_id = orchestrator.create_session(title="CLI Chat (Cleared)")
        context['session_id'] = new_session_id
        console.print("[green]✓ Conversation history cleared (New session started).[/green]")
    return True

def handle_quit(args: List[str], context: Dict[str, Any]) -> bool:
    """Exit chat"""
    console = context.get('console', Console())
    console.print("[dim]Goodbye![/dim]")
    return False

def handle_theme(args: List[str], context: Dict[str, Any]) -> bool:
    """Show or change theme"""
    console = context.get('console', Console())
    if not args:
        available = ", ".join([t.value for t in ThemeName])
        console.print(f"[dim]Available themes: {available}[/dim]")
        console.print(f"[dim]Current theme: {ui_state.theme.value}[/dim]")
        console.print("[dim]Usage: /theme <name>[/dim]")
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
    """Show or change model"""
    console = context.get('console', Console())
    orchestrator = context.get('orchestrator')
    if not orchestrator:
        return True

    if not args:
        current_model = getattr(orchestrator, 'model', 'default')
        console.print(f"[dim]Current model: {current_model}[/dim]")
        console.print("[dim]Usage: /model <model_name>[/dim]")
    else:
        new_model = args[0]
        orchestrator.model = new_model
        # Reflect change to each agent's runtime
        for runtime in orchestrator.runtimes.values():
            if hasattr(runtime, 'model_name'):
                runtime.model_name = new_model
            else:
                runtime.model = new_model
        console.print(f"[green]Model changed to: {new_model}[/green]")
    return True

def handle_profile(args: List[str], context: Dict[str, Any]) -> bool:
    """Show or change profile"""
    console = context.get('console', Console())
    orchestrator = context.get('orchestrator')
    if not orchestrator:
        return True

    if not args:
        from .cli import get_available_profiles
        profiles = get_available_profiles()
        console.print(f"[bold]Current profile:[/bold] {orchestrator.profile}")
        console.print("\n[bold]Available profiles:[/bold]")
        for p in profiles:
            marker = "→" if p == orchestrator.profile else " "
            console.print(f"  {marker} {p}")
        console.print("\n[dim]Usage: /profile <profile_name>[/dim]")
    else:
        new_profile = args[0]
        # Profile change requires re-initialization of Orchestrator
        console.print(f"[dim]Changing profile to '{new_profile}'...[/dim]")
        if hasattr(orchestrator, "set_profile"):
            orchestrator.set_profile(new_profile)
        else:
            orchestrator.profile = new_profile
            orchestrator.loader.profile = new_profile
            orchestrator.skill_loader.profile = new_profile
            if hasattr(orchestrator, 'optimizer_config'):
                orchestrator.optimizer_config.profile = new_profile
            orchestrator.reload_agents()
        console.print(f"[green]Profile changed to: {new_profile}[/green]")
    return True

def handle_workdir(args: List[str], context: Dict[str, Any]) -> bool:
    """Show or change working directory, and manage bookmarks"""
    import os
    import json
    from pathlib import Path
    console = context.get('console', Console())
    orchestrator = context.get('orchestrator')
    if not orchestrator:
        return True

    # Path for saving bookmarks
    moco_home = Path.home() / ".moco"
    bookmarks_file = moco_home / "bookmarks.json"
    
    def load_bookmarks():
        if bookmarks_file.exists():
            try:
                with open(bookmarks_file, 'r') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_bookmarks(bookmarks):
        moco_home.mkdir(exist_ok=True)
        with open(bookmarks_file, 'w') as f:
            json.dump(bookmarks, f, indent=2)

    bookmarks = load_bookmarks()

    if not args:
        current_dir = orchestrator.working_directory
        console.print(f"[bold]Current working directory:[/bold] {current_dir}")
        if bookmarks:
            console.print("\n[bold]Bookmarks:[/bold]")
            for name, path in sorted(bookmarks.items()):
                console.print(f"  [cyan]{name}[/cyan] -> {path}")
        console.print("\n[dim]Usage:[/dim]")
        console.print("  /workdir <path or bookmark>")
        console.print("  /cd <path or bookmark>")
        console.print("  /workdir add <name> [path]    (path defaults to current)")
        console.print("  /workdir remove <name>")
        console.print("  /workdir list")
        return True

    cmd = args[0].lower()
    
    if cmd == "add" and len(args) >= 2:
        name = args[1]
        path_str = args[2] if len(args) > 2 else orchestrator.working_directory
        abs_path = str(Path(path_str).resolve())
        bookmarks[name] = abs_path
        save_bookmarks(bookmarks)
        console.print(f"[green]Bookmark added: {name} -> {abs_path}[/green]")
        return True
    
    if cmd == "remove" and len(args) >= 2:
        name = args[1]
        if name in bookmarks:
            del bookmarks[name]
            save_bookmarks(bookmarks)
            console.print(f"[green]Bookmark removed: {name}[/green]")
        else:
            console.print(f"[yellow]Bookmark not found: {name}[/yellow]")
        return True
    
    if cmd == "list":
        if not bookmarks:
            console.print("[dim]No bookmarks saved.[/dim]")
        else:
            table = Table(title="Directory Bookmarks", border_style="cyan")
            table.add_column("Name", style="cyan")
            table.add_column("Path")
            for name, path in bookmarks.items():
                table.add_row(name, path)
            console.print(table)
        return True

    # Process as bookmark name or path
    target = args[0]
    if target in bookmarks:
        new_dir = bookmarks[target]
    else:
        new_dir = target

    path = Path(new_dir).resolve()
    if not path.is_dir():
        console.print(f"[red]Error: Directory does not exist: {new_dir}[/red]")
        return True

    orchestrator.working_directory = str(path)
    os.environ['MOCO_WORKING_DIRECTORY'] = str(path)
    # Reflect working_directory to each runtime
    for runtime in orchestrator.runtimes.values():
        if hasattr(runtime, 'working_directory'):
            runtime.working_directory = str(path)

    console.print(f"[green]Working directory changed to: {path}[/green]")
    return True

def handle_session(args: List[str], context: Dict[str, Any]) -> bool:
    """Show session information"""
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
    """Save current session (Notification only, as Moco autosaves)"""
    console = console = context.get('console', Console())
    console.print("[green]Session is automatically saved.[/green]")
    return True

def handle_cost(args: List[str], context: Dict[str, Any]) -> bool:
    """Show estimated cost for this session or day.
    
    Usage:
      /cost          -> Current session cost
      /cost today    -> Today's total cost
      /cost all      -> Last 7 days breakdown by model
    """
    from moco.storage.usage_store import get_usage_store
    from rich.table import Table
    from datetime import datetime, timezone
    
    console = context.get('console', Console())
    store = get_usage_store()
    
    period = args[0].lower() if args else "session"
    
    if period == "today":
        # Today's total
        stats = store.get_usage_summary(days=1)
        title = "Today's Total Cost"
        if stats:
            data = stats[-1] # Latest day (today)
        else:
            data = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
    elif period == "all" or period == "week":
        # Last 7 days breakdown
        breakdown = store.get_breakdown(days=7, group_by="model")
        title = "Last 7 Days Cost Breakdown"
        
        if not breakdown:
            console.print("[yellow]No usage data found for the last 7 days.[/yellow]")
            return True
            
        table = Table(title=title, border_style="green")
        table.add_column("Model", style="cyan")
        table.add_column("Input", justify="right")
        table.add_column("Output", justify="right")
        table.add_column("Cost (USD)", justify="right", style="green")
        
        total_cost = 0
        for item in breakdown:
            table.add_row(
                item["label"],
                f"{item['input_tokens']:,}",
                f"{item['output_tokens']:,}",
                f"${item['cost_usd']:.6f}"
            )
            total_cost += item["cost_usd"]
        
        table.add_section()
        table.add_row("Total", "", "", f"${total_cost:.6f}", style="bold")
        console.print(table)
        return True
    else:
        # Default: current session
        session_id = context.get("session_id")
        if not session_id:
            console.print("[yellow]No active session to calculate cost.[/yellow]")
            return True
        
        data = store.get_session_usage(session_id)
        title = f"Current Session Cost"
        if session_id:
            title += f" [dim]({session_id})[/dim]"

    # Display summary table for session or today
    table = Table(title=title, border_style="green")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    
    table.add_row("Total Cost", f"${data['cost_usd']:.6f}", style="bold green")
    table.add_row("Total Tokens", f"{data['total_tokens']:,}")
    table.add_row("  - Input", f"{data['input_tokens']:,}")
    table.add_row("  - Output", f"{data['output_tokens']:,}")
    
    console.print(table)
    return True

def handle_tools(args: List[str], context: Dict[str, Any]) -> bool:
    """Show available tools"""
    console = context.get('console', Console())
    orchestrator = context.get('orchestrator')
    if not orchestrator:
        return True

    # Async-input / plain output mode: avoid Rich tables (ANSI artifacts in some terminals).
    if context.get("plain_output"):
        _print_out(context, "Available Tools:")
        if hasattr(orchestrator, 'tool_map'):
            for tool_name in sorted(orchestrator.tool_map.keys()):
                _print_out(context, f"  - {tool_name}")
        return True

    table = Table(title="Available Tools", border_style="green")
    table.add_column("Tool Name", style="cyan")
    
    if hasattr(orchestrator, 'tool_map'):
        for tool_name in sorted(orchestrator.tool_map.keys()):
            table.add_row(tool_name)
    
    console.print(table)
    return True

def handle_agents(args: List[str], context: Dict[str, Any]) -> bool:
    """Show available agents"""
    console = context.get('console', Console())
    orchestrator = context.get('orchestrator')
    if not orchestrator:
        return True

    # Async-input / plain output mode: avoid Rich tables (ANSI artifacts in some terminals).
    if context.get("plain_output"):
        _print_out(context, "Available Agents:")
        if hasattr(orchestrator, 'agents'):
            for name, config in orchestrator.agents.items():
                desc = getattr(config, "description", "") or ""
                desc = desc.replace("\n", " ").strip()
                if len(desc) > 120:
                    desc = desc[:117] + "..."
                _print_out(context, f"  - {name}: {desc}")
        return True

    table = Table(title="Available Agents", border_style="magenta")
    table.add_column("Agent Name", style="cyan")
    table.add_column("Description")
    
    if hasattr(orchestrator, 'agents'):
        for name, config in orchestrator.agents.items():
            desc = config.description[:50] + "..." if len(config.description) > 50 else config.description
            table.add_row(name, desc)
    
    console.print(table)
    return True
