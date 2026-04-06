# -*- coding: utf-8 -*-
"""mf_media_engine_notify.py

Compatibilità: callback IMFMediaEngineNotify e utilità correlate.

L'implementazione attuale della callback COM è basata su `comtypes` ed è fornita da:
  - `src.video.component_adapter.media_engine_events._MediaEngineNotifyCOM`

Questo modulo mantiene API di compatibilità:
- `PyMediaEngineNotify` (alias dell'implementazione corrente)
- `create_media_engine_notify()`
- `get_event_name()`
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from .mf_base import (
    MF_MEDIA_ENGINE_EVENT_LOADSTART,
    MF_MEDIA_ENGINE_EVENT_PROGRESS,
    MF_MEDIA_ENGINE_EVENT_SUSPEND,
    MF_MEDIA_ENGINE_EVENT_ABORT,
    MF_MEDIA_ENGINE_EVENT_ERROR,
    MF_MEDIA_ENGINE_EVENT_EMPTIED,
    MF_MEDIA_ENGINE_EVENT_STALLED,
    MF_MEDIA_ENGINE_EVENT_PLAY,
    MF_MEDIA_ENGINE_EVENT_PAUSE,
    MF_MEDIA_ENGINE_EVENT_LOADEDMETADATA,
    MF_MEDIA_ENGINE_EVENT_LOADEDDATA,
    MF_MEDIA_ENGINE_EVENT_WAITING,
    MF_MEDIA_ENGINE_EVENT_PLAYING,
    MF_MEDIA_ENGINE_EVENT_CANPLAY,
    MF_MEDIA_ENGINE_EVENT_CANPLAYTHROUGH,
    MF_MEDIA_ENGINE_EVENT_SEEKING,
    MF_MEDIA_ENGINE_EVENT_SEEKED,
    MF_MEDIA_ENGINE_EVENT_TIMEUPDATE,
    MF_MEDIA_ENGINE_EVENT_ENDED,
    MF_MEDIA_ENGINE_EVENT_RATECHANGE,
    MF_MEDIA_ENGINE_EVENT_DURATIONCHANGE,
    MF_MEDIA_ENGINE_EVENT_VOLUMECHANGE,
)

from .component_adapter.media_engine_events import _MediaEngineNotifyCOM

logger = logging.getLogger(__name__)

_EVENT_NAMES: Dict[int, str] = {
    MF_MEDIA_ENGINE_EVENT_LOADSTART: "LOADSTART",
    MF_MEDIA_ENGINE_EVENT_PROGRESS: "PROGRESS",
    MF_MEDIA_ENGINE_EVENT_SUSPEND: "SUSPEND",
    MF_MEDIA_ENGINE_EVENT_ABORT: "ABORT",
    MF_MEDIA_ENGINE_EVENT_ERROR: "ERROR",
    MF_MEDIA_ENGINE_EVENT_EMPTIED: "EMPTIED",
    MF_MEDIA_ENGINE_EVENT_STALLED: "STALLED",
    MF_MEDIA_ENGINE_EVENT_PLAY: "PLAY",
    MF_MEDIA_ENGINE_EVENT_PAUSE: "PAUSE",
    MF_MEDIA_ENGINE_EVENT_LOADEDMETADATA: "LOADEDMETADATA",
    MF_MEDIA_ENGINE_EVENT_LOADEDDATA: "LOADEDDATA",
    MF_MEDIA_ENGINE_EVENT_WAITING: "WAITING",
    MF_MEDIA_ENGINE_EVENT_PLAYING: "PLAYING",
    MF_MEDIA_ENGINE_EVENT_CANPLAY: "CANPLAY",
    MF_MEDIA_ENGINE_EVENT_CANPLAYTHROUGH: "CANPLAYTHROUGH",
    MF_MEDIA_ENGINE_EVENT_SEEKING: "SEEKING",
    MF_MEDIA_ENGINE_EVENT_SEEKED: "SEEKED",
    MF_MEDIA_ENGINE_EVENT_TIMEUPDATE: "TIMEUPDATE",
    MF_MEDIA_ENGINE_EVENT_ENDED: "ENDED",
    MF_MEDIA_ENGINE_EVENT_RATECHANGE: "RATECHANGE",
    MF_MEDIA_ENGINE_EVENT_DURATIONCHANGE: "DURATIONCHANGE",
    MF_MEDIA_ENGINE_EVENT_VOLUMECHANGE: "VOLUMECHANGE",
}


def _to_event_id(value: Any) -> int:
    """Converte un id evento (int/ctypes/oggetti numerici) in int (best effort)."""
    try:
        # ctypes scalari spesso espongono .value
        if hasattr(value, "value"):
            return int(value.value)
        return int(value)
    except (TypeError, ValueError):
        return 0


def get_event_name(event_id: Any) -> str:
    """Ritorna il nome leggibile dell'evento MediaEngine.

    Args:
        event_id: int o valore convertibile a int (inclusi ctypes scalari).

    Returns:
        Nome evento noto (es. "PLAYING") oppure "EVENT_<id>".
    """
    eid = _to_event_id(event_id)
    return _EVENT_NAMES.get(eid, f"EVENT_{eid}")


# Alias di compatibilità: l'implementazione corrente è basata su comtypes.
PyMediaEngineNotify = _MediaEngineNotifyCOM


def create_media_engine_notify(adapter: Any) -> PyMediaEngineNotify:
    """Factory di compatibilità per l'oggetto notify.

    Nota: se `comtypes` non è disponibile, `_MediaEngineNotifyCOM` solleverà.
    """
    return PyMediaEngineNotify(adapter)


__all__ = [
    "PyMediaEngineNotify",
    "create_media_engine_notify",
    "get_event_name",
    "_EVENT_NAMES",
]
