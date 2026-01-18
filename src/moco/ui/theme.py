from dataclasses import dataclass
from enum import Enum

class ThemeName(str, Enum):
    DEFAULT = "default"
    OCEAN = "ocean"
    FOREST = "forest"

@dataclass
class Theme:
    status: str
    tools: str
    thoughts: str
    result: str
    accent: str = "bright_cyan"
    muted: str = "grey50"
    success: str = "green"
    warning: str = "yellow"
    error: str = "red"

THEMES = {
    ThemeName.DEFAULT: Theme(
        status="cyan",
        tools="blue",
        thoughts="magenta",
        result="green",
        accent="bright_cyan",
        muted="grey50",
        success="green",
        warning="yellow",
        error="red",
    ),
    ThemeName.OCEAN: Theme(
        status="bright_blue",
        tools="cyan",
        thoughts="white",
        result="blue",
        accent="bright_cyan",
        muted="grey50",
        success="bright_green",
        warning="yellow",
        error="red",
    ),
    ThemeName.FOREST: Theme(
        status="green",
        tools="yellow",
        thoughts="white",
        result="bright_green",
        accent="bright_green",
        muted="grey50",
        success="green",
        warning="yellow",
        error="red",
    ),
}
