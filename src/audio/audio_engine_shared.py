from __future__ import annotations

VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}


def normalize_loop_position(position: float, duration: float, loop_enabled: bool) -> float:
    """Clamp and wrap a playback position when engine-level looping is active."""
    try:
        pos_f = float(position)
    except (TypeError, ValueError):
        return 0.0

    if pos_f < 0.0:
        pos_f = 0.0

    try:
        dur_f = float(duration)
    except (TypeError, ValueError):
        dur_f = 0.0

    if loop_enabled and dur_f > 0.0 and pos_f >= dur_f:
        pos_f = pos_f % dur_f

    return pos_f
