from __future__ import annotations

from .console import console
from .layout import ui_state, refresh


def set_result(text: str) -> None:
    """Set the final result text for the bottom panel and refresh."""
    ui_state.result = text
    refresh()


def print_final_result() -> None:
    """Print the final result to the console outside of the live UI.

    This is useful at the very end of execution so that the result remains
    visible even after the live layout is cleared.
    """
    if ui_state.result is not None:
        console.print(ui_state.result)
