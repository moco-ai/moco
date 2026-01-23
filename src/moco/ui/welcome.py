import os
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.columns import Columns
from rich.table import Table
from rich.text import Text
from rich import box


def show_welcome_dashboard(orchestrator, theme_config):
    """
    Orchestratorインスタンスから実際のデータを取得してダッシュボードを表示する。
    """
    console = Console()

    # 実際の実装からデータを抽出
    profile = getattr(orchestrator, "profile", "default")
    model = getattr(orchestrator, "model", None)
    provider = getattr(orchestrator, "provider", None)
    work_dir = getattr(orchestrator, "working_directory", os.getcwd())

    # セッションIDの取得（未設定でも落ちないようにする）
    session_id = getattr(orchestrator, "_current_session_id", None)
    if not session_id and hasattr(orchestrator, "session_logger"):
        try:
            sessions = orchestrator.session_logger.list_sessions(limit=1)
            if sessions:
                session_id = sessions[0].get("session_id")
        except Exception:
            pass

    # エージェント・ツール・スキルの情報
    agents = getattr(orchestrator, "agents", {}) or {}
    agent_names = [name for name in agents.keys() if name != "orchestrator"]
    tools_count = len(getattr(orchestrator, "tool_map", {}) or {})
    skills_count = len(getattr(orchestrator, "_all_skills", {}) or {})
    use_optimizer = bool(getattr(orchestrator, "use_optimizer", False))

    # 1. Header (Moco Identity)
    header_text = Text.assemble(
        (" MOCO AGENT ", f"bold white on {theme_config.status}"),
        " ",
        ("● ONLINE", "green"),
        "  ",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "dim"),
    )
    console.print(Panel(header_text, style=theme_config.status, box=box.ROUNDED))

    # 2. Context & Runtime Panels
    context_info = Table.grid(expand=True)
    context_info.add_row(f"[bold {theme_config.status}]Profile:[/]", f" {profile}")
    context_info.add_row(f"[bold {theme_config.status}]WorkDir:[/]", f" [dim]{work_dir}[/dim]")
    if isinstance(session_id, str) and session_id:
        context_info.add_row(f"[bold {theme_config.status}]Session:[/]", f" {session_id[:12]}...")
    else:
        context_info.add_row(f"[bold {theme_config.status}]Session:[/]", " [dim](not set)[/dim]")

    runtime_info = Table.grid(expand=True)
    runtime_info.add_row(f"[bold {theme_config.thoughts}]Model:[/]", f" {model}")
    runtime_info.add_row(f"[bold {theme_config.thoughts}]Provider:[/]", f" {provider}")
    runtime_info.add_row(
        f"[bold {theme_config.thoughts}]Optimizer:[/]",
        f" {'[green]Enabled[/]' if use_optimizer else '[dim]Disabled[/]'}",
    )

    panels = [
        Panel(context_info, title="Context", border_style=theme_config.status, expand=True),
        Panel(runtime_info, title="Runtime", border_style=theme_config.thoughts, expand=True),
    ]
    console.print(Columns(panels))

    # 3. Inventory (Agents & Skills)
    inventory_table = Table(
        box=box.SIMPLE, show_header=True, header_style=f"bold {theme_config.accent}", expand=True
    )
    inventory_table.add_column("Type", ratio=1)
    inventory_table.add_column("Count / Details", ratio=3)

    inventory_table.add_row("Agents", f"[cyan]{', '.join(agent_names) if agent_names else 'None'}[/cyan]")
    inventory_table.add_row("Tools", f"{tools_count} capabilities loaded")
    inventory_table.add_row("Skills", f"{skills_count} specialized skills ready")

    console.print(Panel(inventory_table, title="Inventory", border_style=theme_config.tools))

    # 4. Footer (Help)
    footer = Text.assemble(
        ("Quick Commands: ", "bold"),
        ("/chat ", f"bold {theme_config.warning}"),
        "Chat ",
        ("/run ", f"bold {theme_config.warning}"),
        "Task ",
        ("/help ", f"bold {theme_config.warning}"),
        "Help ",
        ("/exit ", "dim"),
        "Quit",
    )
    console.print(Panel(footer, style="dim", box=box.HORIZONTALS))
    console.print()
