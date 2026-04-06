from __future__ import annotations

import ctypes
import threading
import weakref
from typing import Any, Callable, Optional

from src.video.component_base.com_helpers import ComPtr
from src.video.component_base.definitions import IMFMediaEngine
from src.video.mf_base import logger

from .media_engine_core_playback import attach_media_engine_core_playback_behavior as _attach_media_engine_core_playback_behavior
from .media_engine_core_setup import attach_media_engine_core_setup_behavior as _attach_media_engine_core_setup_behavior
from .media_engine_core_shared import MediaEngineError
from .media_engine_core_shutdown import attach_media_engine_core_shutdown_behavior as _attach_media_engine_core_shutdown_behavior
from .media_engine_core_vtable import attach_media_engine_core_vtable_behavior as _attach_media_engine_core_vtable_behavior

MEDIA_ENGINE_CORE_DEL_EXCEPTIONS = (AttributeError, MediaEngineError, OSError, RuntimeError, TypeError, ValueError)


class MediaEngineCore:
    """Wrapper per IMFMediaEngine. Deve vivere e operare solo nel thread COM."""

    def __init__(self, adapter: Any) -> None:
        self._adapter_ref = weakref.ref(adapter)

        self._media_engine: Optional[ctypes.POINTER(IMFMediaEngine)] = None
        self._attributes: Optional[ComPtr] = None
        self._factory: Optional[ComPtr] = None
        self._media_engine_ex: Optional[Any] = None

        self._notify_handler: Optional[Any] = None
        self._notify_iunknown: Optional[Any] = None

        self._vtable_call_cache: dict[tuple[int, Any, tuple[Any, ...]], Any] = {}
        self._source: Optional[str] = None
        self._requested_source: Optional[str] = None
        self._active_source: Optional[str] = None
        self._shutdown_requested: bool = False
        self._playback_hwnd: Optional[int] = None
        self._engine_generation: int = 0
        self._state_lock = threading.RLock()

        logger.info("[MediaEngineCore] Istanza creata.")

    def __del__(self) -> None:
        try:
            if not getattr(self, "_shutdown_requested", True):
                self.shutdown()
        except MEDIA_ENGINE_CORE_DEL_EXCEPTIONS as error:
            logger.debug("[MediaEngineCore] __del__ shutdown failed: %r", error, exc_info=True)


_MEDIA_ENGINE_CORE_ATTACHERS: tuple[Callable[[type["MediaEngineCore"]], None], ...] = (
    _attach_media_engine_core_setup_behavior,
    _attach_media_engine_core_vtable_behavior,
    _attach_media_engine_core_playback_behavior,
    _attach_media_engine_core_shutdown_behavior,
)


def attach_media_engine_core_behavior(cls: type["MediaEngineCore"]) -> None:
    """Attach MediaEngineCore behavior in one central place.

    Edge cases:
        - Duplicate attachment during reloads can silently rebind methods.
        - Partial split imports can leave the class only partially patched.
        - Reordered installers can break setup or shutdown assumptions.
    """
    if getattr(cls, "_media_engine_core_behavior_attached", False):
        return

    for installer in _MEDIA_ENGINE_CORE_ATTACHERS:
        installer(cls)

    setattr(cls, "_media_engine_core_behavior_attached", True)


attach_media_engine_core_behavior(MediaEngineCore)
