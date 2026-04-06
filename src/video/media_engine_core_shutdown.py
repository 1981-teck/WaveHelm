from __future__ import annotations

import ctypes
import logging
from typing import Any, Optional

from src.video.component_base.com_helpers import ComPtr
from src.video.component_base.definitions import IMFMediaEngine

logger = logging.getLogger(__name__)

MEDIA_ENGINE_CORE_SHUTDOWN_SYNC_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError)
MEDIA_ENGINE_CORE_SHUTDOWN_ASYNC_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError)


def shutdown(self) -> None:
    """Rilascia le risorse COM (best effort, idempotente)."""
    with self._state_lock:
        if self._shutdown_requested:
            return
        self._shutdown_requested = True

        media_engine = self._media_engine
        factory = self._factory
        media_engine_ex = self._media_engine_ex
        attributes = self._attributes
        notify_iunknown = self._notify_iunknown
        notify_handler = self._notify_handler

        self._media_engine = None
        self._factory = None
        self._media_engine_ex = None
        self._attributes = None
        self._notify_iunknown = None
        self._notify_handler = None
        self._vtable_call_cache.clear()

    logger.info("[MediaEngineCore] Inizio shutdown.")

    adapter_obj = self._adapter_ref()
    if adapter_obj:
        try:
            adapter_obj.call_on_com_thread(
                "shutdown",
                lambda: self._shutdown_and_release_on_com_thread(
                    media_engine, media_engine_ex, factory, attributes, notify_iunknown, notify_handler
                ),
            )
            logger.info("[MediaEngineCore] Shutdown completato su thread COM.")
            return
        except MEDIA_ENGINE_CORE_SHUTDOWN_SYNC_EXCEPTIONS:
            logger.exception("[MediaEngineCore] Shutdown sincrono su thread COM fallito; provo async/best-effort.")

        try:
            adapter_obj.post_to_com_thread(
                "shutdown_async",
                lambda: self._shutdown_and_release_on_com_thread(
                    media_engine, media_engine_ex, factory, attributes, notify_iunknown, notify_handler
                ),
            )
            logger.info("[MediaEngineCore] Shutdown schedulato su thread COM (async/best-effort).")
            return
        except MEDIA_ENGINE_CORE_SHUTDOWN_ASYNC_EXCEPTIONS:
            logger.exception("[MediaEngineCore] Impossibile schedulare shutdown sul thread COM; fallback locale.")

    self._shutdown_and_release_local(
        media_engine,
        media_engine_ex,
        factory,
        attributes,
        notify_iunknown,
        notify_handler,
    )


def _shutdown_and_release_on_com_thread(
    self,
    media_engine: Optional[ctypes.POINTER(IMFMediaEngine)],
    media_engine_ex: Optional[Any],
    factory: Optional[ComPtr],
    attributes: Optional[ComPtr],
    notify_iunknown: Optional[Any],
    notify_handler: Optional[Any],
) -> None:
    try:
        from src.video.component_base.utils import safe_release
        self._stop_engine_ptr_playback(media_engine)
        self._shutdown_engine_ptr(media_engine)
        safe_release(media_engine_ex, "media engine ex")
        safe_release(media_engine, "media engine")
        safe_release(factory, "factory")
        safe_release(attributes, "attributes")
    finally:
        _ = notify_handler
        self._release_notify_iunknown(notify_iunknown)


def _shutdown_and_release_local(
    self,
    media_engine: Optional[ctypes.POINTER(IMFMediaEngine)],
    media_engine_ex: Optional[Any],
    factory: Optional[ComPtr],
    attributes: Optional[ComPtr],
    notify_iunknown: Optional[Any],
    notify_handler: Optional[Any],
) -> None:
    try:
        from src.video.component_base.utils import safe_release
        self._stop_engine_ptr_playback(media_engine)
        self._shutdown_engine_ptr(media_engine)
        safe_release(media_engine_ex, "media engine ex")
        safe_release(media_engine, "media engine")
        safe_release(factory, "factory")
        safe_release(attributes, "attributes")
    finally:
        _ = notify_handler
        self._release_notify_iunknown(notify_iunknown)


_MEDIA_ENGINE_CORE_SHUTDOWN_METHODS = (
    ("shutdown", shutdown),
    ("_shutdown_and_release_on_com_thread", _shutdown_and_release_on_com_thread),
    ("_shutdown_and_release_local", _shutdown_and_release_local),
)


def install_media_engine_core_shutdown_behavior(cls) -> None:
    """Install MediaEngineCore shutdown behavior on the central class.

    Edge cases:
        - Re-running the installer during reloads can silently override shutdown hooks.
        - Partial split imports can leave the coordinator with incomplete shutdown wiring.
        - Legacy leaf entrypoints can diverge from the central shutdown contract over time.
    """
    if getattr(cls, "_media_engine_core_shutdown_behavior_attached", False):
        return

    for name, method in _MEDIA_ENGINE_CORE_SHUTDOWN_METHODS:
        setattr(cls, name, method)

    setattr(cls, "_media_engine_core_shutdown_behavior_attached", True)


def attach_media_engine_core_shutdown_behavior(cls) -> None:
    """Compatibility shim for historical attach_* imports.

    Prefer install_media_engine_core_shutdown_behavior() from the central coordinator path.
    """
    install_media_engine_core_shutdown_behavior(cls)
