from __future__ import annotations

from typing import Optional

from .layout import ui_state, refresh


def add_tool_summary(name: str, status: str, duration: Optional[float] = None) -> None:
    """Append a tool summary row and refresh UI."""
    ui_state.tools_summary.append((name, status, duration))
    refresh()


def set_tool_detail(name: str, detail: str) -> None:
    """Set detailed information for a tool.

    Also remembers the last updated tool name so the layout can highlight its
    details without changing the public API.
    """
    ui_state.tool_details[name] = detail
    ui_state.last_tool_detail_name = name
    refresh()
