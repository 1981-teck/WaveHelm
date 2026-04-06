from __future__ import annotations

from enum import Enum, auto


class VideoState(Enum):
    """Stati del controller video."""
    IDLE = auto()
    READY = auto()
    PLAYING = auto()
    PAUSED = auto()
    STOPPED = auto()
    ERROR = auto()
