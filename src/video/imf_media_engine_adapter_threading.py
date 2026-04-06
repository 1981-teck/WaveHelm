from __future__ import annotations

from typing import Any, Callable

from .mf_base import logger

IMF_ADAPTER_COM_THREAD_START_EXCEPTIONS = (OSError, RuntimeError, TimeoutError, TypeError, ValueError)
IMF_ADAPTER_COM_THREAD_POST_EXCEPTIONS = (OSError, RuntimeError, TimeoutError, TypeError, ValueError)


def _get_com_thread_manager(self, *, ensure_started: bool = True):
    """Ottiene l'istanza ComThreadManager (lazy import, thread-safe)."""
    with self._lock:
        mgr = self._com_thread_manager
        shutdown = self._shutdown_requested or self._closed

    if mgr is None:
        with self._lock:
            mgr = self._com_thread_manager
            if mgr is None:
                from .component_adapter.com_thread_manager import (
                    com_thread_manager as _com_thread_manager,
                )

                mgr = _com_thread_manager
                self._com_thread_manager = mgr

    if ensure_started:
        with self._lock:
            shutdown = self._shutdown_requested or self._closed
        if shutdown:
            raise RuntimeError("COM thread start richiesto durante shutdown/closed")

        try:
            mgr.start()
        except IMF_ADAPTER_COM_THREAD_START_EXCEPTIONS as exc:
            logger.error("[IMFAdapter] Impossibile avviare il thread COM: %s", exc, exc_info=True)
            raise

    return mgr


def post_to_com_thread(self, name: str, func: Callable) -> None:
    """Inoltra una chiamata asincrona (fire-and-forget) al ComThreadManager."""
    with self._lock:
        ensure_started = not (self._shutdown_requested or self._closed)

    try:
        mgr = self._get_com_thread_manager(ensure_started=ensure_started)
        mgr.post_to_com_thread(name, func)
    except IMF_ADAPTER_COM_THREAD_POST_EXCEPTIONS:
        logger.debug("[IMFAdapter] post_to_com_thread('%s') failed (best effort)", name, exc_info=True)


def call_on_com_thread(self, name: str, func: Callable) -> Any:
    """Inoltra la chiamata sincrona al ComThreadManager usando l'istanza corretta."""
    with self._lock:
        ensure_started = not (self._shutdown_requested or self._closed)

    mgr = self._get_com_thread_manager(ensure_started=ensure_started)
    return mgr.call_on_com_thread(name, func)


_IMF_MEDIA_ENGINE_ADAPTER_THREADING_METHODS: tuple[tuple[str, Callable[..., Any]], ...] = (
    ("_get_com_thread_manager", _get_com_thread_manager),
    ("post_to_com_thread", post_to_com_thread),
    ("call_on_com_thread", call_on_com_thread),
)


def install_imf_media_engine_adapter_threading_behavior(cls) -> None:
    """Install threading behavior on the adapter class.

    Edge cases:
        - Duplicate installer calls can silently rebind thread helpers.
        - Shutdown/closed state must prevent implicit COM thread startup.
        - Late lazy imports can fail if the COM thread manager module is unavailable.
    """
    if getattr(cls, "_imf_media_engine_adapter_threading_behavior_attached", False):
        return

    for name, method in _IMF_MEDIA_ENGINE_ADAPTER_THREADING_METHODS:
        setattr(cls, name, method)

    setattr(cls, "_imf_media_engine_adapter_threading_behavior_attached", True)



def attach_imf_media_engine_adapter_threading_behavior(cls) -> None:
    """Compatibility shim for legacy attach_* imports."""
    install_imf_media_engine_adapter_threading_behavior(cls)
