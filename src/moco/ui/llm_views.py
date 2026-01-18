from __future__ import annotations

from .layout import ui_state, refresh


def append_thought(text: str) -> None:
    """Append a new thought line to the middle panel and refresh."""
    ui_state.thoughts.append(text)
    refresh()


def stream_llm_token(token: str) -> None:
    """Append streaming LLM tokens to the last thought.

    If there is no existing thought line, a new one is created first.
    """
    if not ui_state.thoughts:
        ui_state.thoughts.append(token)
    else:
        ui_state.thoughts[-1] += token
    refresh()
