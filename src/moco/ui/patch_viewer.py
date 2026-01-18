import sys
import difflib
from pathlib import Path
from typing import Optional, List, Tuple
from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

def get_patch_dir() -> Path:
    """パッチ保存ディレクトリを取得（なければ作成）"""
    patch_dir = Path(".moco/patches")
    patch_dir.mkdir(parents=True, exist_ok=True)
    return patch_dir

def preview_patch(
    filename: str, 
    old_content: str, 
    new_content: str, 
    title: Optional[str] = None
) -> str:
    """
    ファイル変更の差分を表示し、ユーザーに承認を求める。
    
    Returns:
        'y': 承認 (Apply)
        'n': 拒否 (Skip)
        'e': 編集 (Edit) - 今回は簡易的に 'n' と同等か、後で拡張
        's': 保存 (Save as patch)
    """
    diff = list(difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    ))

    if not diff:
        console.print(f"[yellow]No changes for {filename}[/yellow]")
        return 'y'

    table = Table(title=title or f"Patch Preview: {filename}", border_style="bright_blue", show_header=False)
    table.add_column("line")

    diff_text = Text()
    for line in diff:
        clean_line = line.rstrip()
        if line.startswith('+') and not line.startswith('+++'):
            diff_text.append(clean_line + "\n", style="green")
        elif line.startswith('-') and not line.startswith('---'):
            diff_text.append(clean_line + "\n", style="red")
        elif line.startswith('@@'):
            diff_text.append(clean_line + "\n", style="cyan")
        elif line.startswith(('---', '+++')):
            diff_text.append(clean_line + "\n", style="bold magenta")
        else:
            diff_text.append(clean_line + "\n")

    table.add_row(diff_text)
    console.print(table)

    while True:
        prompt = "[bold bright_white]Apply this change?[/] [green](y)es[/] / [red](n)o[/] / [blue](s)ave patch[/]: "
        choice = console.input(prompt).lower().strip()
        if choice in ('y', 'n', 's'):
            return choice
        console.print("[dim]Please enter y, n, or s.[/dim]")

def save_patch(filename: str, old_content: str, new_content: str, patch_name: Optional[str] = None):
    """パッチを .moco/patches/ に保存する"""
    import time
    import uuid
    diff = list(difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    ))
    
    if not patch_name:
        timestamp = int(time.time())
        random_suffix = uuid.uuid4().hex[:6]
        safe_name = filename.replace("/", "_").replace("\\", "_")
        patch_name = f"{safe_name}_{timestamp}_{random_suffix}.patch"
    
    patch_path = get_patch_dir() / patch_name
    with open(patch_path, "w", encoding="utf-8") as f:
        f.writelines(diff)
    
    console.print(f"[green]Patch saved to {patch_path}[/green]")
    return patch_path

def apply_patch_file(patch_path: Path):
    """保存されたパッチを適用する"""
    import subprocess
    import shutil
    
    if not shutil.which("patch"):
        console.print("[red]Error: 'patch' command not found in your system PATH.[/red]")
        console.print("[yellow]Please install 'patch' (e.g., 'brew install patch' or 'apt-get install patch').[/yellow]")
        return False

    try:
        # patch -p1 < patch_file
        result = subprocess.run(
            ["patch", "-p1"],
            input=patch_path.read_text(),
            text=True,
            capture_output=True
        )
        if result.returncode == 0:
            console.print(f"[green]Successfully applied patch: {patch_path}[/green]")
            return True
        else:
            console.print(f"[red]Failed to apply patch: {patch_path}[/red]")
            console.print(result.stderr)
            return False
    except Exception as e:
        console.print(f"[red]Error applying patch: {e}[/red]")
        return False
