from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

from rich.markup import escape
from rich.console import RenderableType
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.spinner import Spinner
from rich.text import Text

from .console import console
from .theme import THEMES, ThemeName


class UIState:
    def __init__(self) -> None:
        self.status: str = ""
        self.spinner_text: str = ""
        self.tools_summary: List[Tuple[str, str, Optional[float]]] = []
        self.tool_details: Dict[str, str] = {}
        self.last_tool_detail_name: Optional[str] = None
        self.thoughts: List[str] = []
        self.result: Optional[str] = None
        self.theme: ThemeName = ThemeName.DEFAULT

    def render(self) -> RenderableType:
        theme = THEMES.get(self.theme, THEMES[ThemeName.DEFAULT])
        layout = Layout()

        # 上・中・下に分割
        layout.split(
            Layout(name="top", ratio=1, minimum_size=10),
            Layout(name="middle", ratio=2),
            Layout(name="bottom", ratio=1),
        )

        # --- 上部: ステータス + ツール一覧 + ツール詳細 ---
        top_layout = Layout()
        top_layout.split_row(
            Layout(name="status", ratio=1),
            Layout(name="tools_section", ratio=3),
        )

        # 1. ステータスパネル
        spinner_text = self.spinner_text or self.status or "実行中..."
        spinner = Spinner("dots", text=escape(spinner_text))
        status_panel = Panel(
            spinner,
            title="ステータス",
            border_style=theme.status,
        )
        top_layout["status"].update(status_panel)

        # 2. ツールセクション (ツール一覧 + ツール詳細)
        tools_section = Layout()
        tools_section.split_row(
            Layout(name="tools_table", ratio=1),
            Layout(name="tool_detail", ratio=1),
        )

        # ツール一覧テーブル
        tools_table = Table(show_header=True, header_style=f"bold {theme.accent}", box=None)
        tools_table.add_column("ツール名", style="bold")
        tools_table.add_column("ステータス")
        tools_table.add_column("実行時間", justify="right")

        for name, status, duration in self.tools_summary:
            duration_str = f"{duration:.2f}s" if duration is not None else "-"
            tools_table.add_row(escape(name), escape(status), duration_str)

        tools_panel = Panel(
            tools_table,
            title="ツール一覧",
            border_style=theme.tools,
        )
        tools_section["tools_table"].update(tools_panel)

        # ツール詳細パネル (最後に更新されたツールを表示)
        if self.last_tool_detail_name and self.last_tool_detail_name in self.tool_details:
            name = self.last_tool_detail_name
            detail = self.tool_details[name]
            detail_content = f"[bold {theme.accent}]{escape(name)}[/]\n\n{escape(detail)}"
        else:
            detail_content = f"[{theme.muted}]詳細情報はまだありません[/]"

        detail_panel = Panel(
            detail_content,
            title="ツール詳細",
            border_style=theme.tools,
        )
        tools_section["tool_detail"].update(detail_panel)

        top_layout["tools_section"].update(tools_section)
        layout["top"].update(top_layout)

        # --- 中央: 思考ログ ---
        if self.thoughts:
            # 1行目は太字、2行目以降は箇条書き
            lines = []
            for i, thought in enumerate(self.thoughts):
                if i == 0:
                    lines.append(f"[bold]{escape(thought)}[/]")
                else:
                    lines.append(f"• {escape(thought)}")
            thoughts_content = "\n".join(lines)
        else:
            thoughts_content = f"[{theme.muted}]思考ログはまだありません[/]"

        thoughts_panel = Panel(
            thoughts_content,
            title="思考ログ",
            border_style=theme.thoughts,
        )
        layout["middle"].update(thoughts_panel)

        # --- 下部: 実行結果 ---
        if not self.result:
            result_content = f"[{theme.muted}]実行結果はまだありません[/]"
            result_border = theme.muted
        elif self.result.startswith("ERROR:"):
            result_content = f"[{theme.error}]✗ {escape(self.result)}[/]"
            result_border = theme.error
        else:
            result_content = f"[{theme.success}]✓ {escape(self.result)}[/]"
            result_border = theme.success

        result_panel = Panel(
            result_content,
            title="実行結果",
            border_style=result_border,
        )
        layout["bottom"].update(result_panel)

        return layout


ui_state = UIState()

_live: Optional[Live] = None


@contextmanager
def live_ui(refresh_per_second: float = 8.0):
    global _live
    with Live(
        ui_state.render(),
        console=console,
        refresh_per_second=refresh_per_second,
        auto_refresh=False,
    ) as live:
        _live = live
        try:
            yield
        finally:
            _live = None


def refresh() -> None:
    if _live is not None:
        _live.update(ui_state.render())
        _live.refresh()
