from __future__ import annotations

from pathlib import Path
from typing import Any


def format_time(seconds: float | None) -> str:
    """Return a stable playback timestamp for mini-player labels."""
    try:
        if seconds is None:
            return '--:--'
        total = int(float(seconds))
    except (OverflowError, TypeError, ValueError):
        return '--:--'
    if total < 0:
        return '--:--'
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f'{hours:d}:{minutes:02d}:{secs:02d}'
    return f'{minutes:d}:{secs:02d}'


def resolve_track_title(track: Any) -> str:
    """Resolve the most useful user-facing title from mixed track payloads."""
    if isinstance(track, dict):
        for key in ('title', 'name', 'display_name'):
            value = track.get(key)
            if value:
                return str(value)
        path_value = track.get('path')
    else:
        for key in ('title', 'name', 'display_name'):
            value = getattr(track, key, None)
            if value:
                return str(value)
        path_value = getattr(track, 'path', None)
    if path_value:
        try:
            return Path(str(path_value)).stem or str(path_value)
        except (OSError, TypeError, ValueError):
            return str(path_value)
    return ''
