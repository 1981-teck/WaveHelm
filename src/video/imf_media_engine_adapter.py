from __future__ import annotations

import queue
import threading
from typing import Callable

from .component_adapter.media_engine_core import MediaEngineCore
from .imf_media_engine_adapter_events import attach_imf_media_engine_adapter_events_behavior as _attach_imf_media_engine_adapter_events_behavior
from .imf_media_engine_adapter_playback import attach_imf_media_engine_adapter_playback_behavior as _attach_imf_media_engine_adapter_playback_behavior
from .imf_media_engine_adapter_threading import attach_imf_media_engine_adapter_threading_behavior as _attach_imf_media_engine_adapter_threading_behavior
from .mf_base import logger


class IMFMediaEngineAdapter:
    """Adapter di alto livello per IMFMediaEngine (HWND-based)."""

    def __init__(self, event_bus: object | None = None) -> None:
        self._event_bus = event_bus
        self._lock = threading.RLock()

        self._hwnd: int = 0
        self._source: str | None = None
        self._loop_enabled: bool = False
        self._volume: float = 1.0
        self._muted: bool = False
        self._event_queue: "queue.Queue[tuple[int, int, int]]" = queue.Queue()

        self._shutdown_requested: bool = False
        self._closed: bool = False

        self._com_thread_manager = None
        self._core = MediaEngineCore(self)

        logger.info("[IMFAdapter] Adapter inizializzato.")


def create_media_engine_adapter(event_bus=None):
    """Factory function per creare un'istanza dell'adapter."""
    return IMFMediaEngineAdapter(event_bus)


_IMF_MEDIA_ENGINE_ADAPTER_ATTACHERS: tuple[Callable[[type["IMFMediaEngineAdapter"]], None], ...] = (
    _attach_imf_media_engine_adapter_threading_behavior,
    _attach_imf_media_engine_adapter_playback_behavior,
    _attach_imf_media_engine_adapter_events_behavior,
)


def attach_imf_media_engine_adapter_behavior(cls: type["IMFMediaEngineAdapter"]) -> None:
    """Attach IMFMediaEngineAdapter behavior in one central place.

    Edge cases:
        - Duplicate attachment during reloads can silently rebind methods.
        - Partial split imports can leave the adapter only partially patched.
        - Reordered installers can break threading, playback, or event wiring.
    """
    if getattr(cls, "_imf_media_engine_adapter_behavior_attached", False):
        return

    for installer in _IMF_MEDIA_ENGINE_ADAPTER_ATTACHERS:
        installer(cls)

    setattr(cls, "_imf_media_engine_adapter_behavior_attached", True)


attach_imf_media_engine_adapter_behavior(IMFMediaEngineAdapter)
