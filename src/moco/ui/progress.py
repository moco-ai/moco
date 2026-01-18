from __future__ import annotations

from .layout import ui_state, refresh


def set_status(status: str, spinner_text: str | None = None) -> None:
    """Update global status and trigger UI refresh.

    Args:
        status: Short human readable status message.
        spinner_text: Optional text to show next to spinner. If omitted,
            ``status`` is reused.
    """
    ui_state.status = status
    if spinner_text is not None:
        ui_state.spinner_text = spinner_text
    else:
        ui_state.spinner_text = status
    refresh()
