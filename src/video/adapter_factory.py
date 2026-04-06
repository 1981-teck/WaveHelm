# -*- coding: utf-8 -*-
"""adapter_factory.py

Factory e singleton manager per l'adapter video basato su Media Foundation (IMFMediaEngine).

Design goals:
- creazione lazy e thread-safe dell'adapter
- reset/shutdown robusti (best effort)
- evitare side-effect pesanti su import (niente MFStartup/COM init qui)

Nota:
- L'adapter è un oggetto Python; non esiste un "AddRef" COM da gestire qui.
  La safety riguarda invece la coerenza dello stato globale (istanza/creating/error/callback)
  e la prevenzione di race/deadlock in scenari multi-thread.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

FACTORY_STATE_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
FACTORY_IMPORT_EXCEPTIONS = (ImportError, AttributeError, OSError, RuntimeError, TypeError, ValueError)


class MediaEngineError(RuntimeError):
    """Errore generico per la creazione/gestione dell'adapter.

    (Compatibilità: alcuni vecchi rami importavano MediaEngineError da adapter_factory.)
    """


@dataclass
class _FactoryState:
    # Singleton instance
    instance: Optional[Any] = None

    # Creation state
    created: bool = False
    creating: bool = False
    creator_tid: Optional[int] = None
    last_error: Optional[str] = None
    creation_seq: int = 0  # monotonic generation per debug

    # Optional callback invoked after successful creation
    creation_callback: Optional[Callable[[Any], None]] = None

    # Prevent shutdown during callback execution (best effort)
    callback_running: bool = False
    callback_tid: Optional[int] = None


_adapter_lock = threading.RLock()
_adapter_cv = threading.Condition(_adapter_lock)
_state = _FactoryState()


def _now() -> float:
    return time.monotonic()


def _is_adapter_closed(adapter: Any) -> bool:
    """Best-effort check per evitare di ritornare un adapter già chiuso.

    Non tutti gli adapter espongono la stessa API; proviamo campi comuni.
    """
    try:
        if adapter is None:
            return True
        # Campi tipici dei nostri adapter
        if bool(getattr(adapter, "_closed", False)):
            return True
        if bool(getattr(adapter, "_shutdown_requested", False)):
            return True
        # Metodi opzionali
        if hasattr(adapter, "is_closed") and callable(getattr(adapter, "is_closed")):
            return bool(adapter.is_closed())
    except FACTORY_STATE_EXCEPTIONS:
        # Se il check fallisce, non blocchiamo: consideriamo l'adapter utilizzabile.
        return False
    return False


def _wait_for_condition_locked(predicate: Callable[[], bool], timeout_s: float) -> bool:
    """Attende che predicate() diventi True, rilasciando il lock durante l'attesa.

    Deve essere chiamato con _adapter_lock acquisito.
    Returns:
        True se predicate() è diventato True entro timeout, False se timeout.
    """
    deadline = _now() + max(0.0, float(timeout_s))
    while not predicate():
        remaining = deadline - _now()
        if remaining <= 0:
            return False
        _adapter_cv.wait(timeout=min(0.5, remaining))
    return True


def set_adapter_creation_callback(callback: Optional[Callable[[Any], None]]) -> None:
    """Imposta un callback invocato quando l'adapter viene creato con successo."""
    with _adapter_lock:
        _state.creation_callback = callback


def is_adapter_created() -> bool:
    """True se l'adapter singleton è stato creato ed è presente."""
    with _adapter_lock:
        return _state.instance is not None and not _is_adapter_closed(_state.instance)


def get_adapter_status() -> dict:
    """Ritorna informazioni diagnostiche sulla factory."""
    with _adapter_lock:
        return {
            "created": bool(_state.created),
            "creating": bool(_state.creating),
            "creator_tid": _state.creator_tid,
            "has_instance": _state.instance is not None,
            "instance_closed": _is_adapter_closed(_state.instance) if _state.instance is not None else None,
            "last_error": _state.last_error,
            "creation_seq": _state.creation_seq,
            "callback_running": bool(_state.callback_running),
            "callback_tid": _state.callback_tid,
        }


def create_imf_media_engine_adapter(event_bus: object | None = None) -> Any:
    """Crea una nuova istanza di IMFMediaEngineAdapter."""
    try:
        from .imf_media_engine_adapter import IMFMediaEngineAdapter
    except FACTORY_IMPORT_EXCEPTIONS as exc:
        raise MediaEngineError(f"Impossibile importare IMFMediaEngineAdapter: {exc}") from exc

    # Firma attesa: IMFMediaEngineAdapter(event_bus=...)
    return IMFMediaEngineAdapter(event_bus=event_bus)


def create_best_video_adapter(event_bus: object | None = None):
    """Crea (se necessario) e ritorna l'adapter singleton 'migliore' disponibile.

    Thread-safety:
    - un solo thread può creare alla volta (others wait su condition)
    - in caso di errore, `last_error` viene reso visibile agli altri thread
    - il callback (se presente) non può essere interrotto da shutdown (best effort)
    """
    cb_to_run: Optional[Callable[[Any], None]] = None
    adapter_for_cb: Any = None

    # Fast-path + gestione concorrente di creazione
    with _adapter_lock:
        # Se già creato e non chiuso, ritorna
        if _state.instance is not None and not _is_adapter_closed(_state.instance):
            return _state.instance

        # Se un'altra chiamata sta creando, attendi
        if _state.creating:
            current_tid = threading.get_ident()
            if _state.creator_tid == current_tid:
                raise MediaEngineError("Re-entrant create_best_video_adapter() during adapter creation")

            logger.info("[Factory] Adapter creation in progress; waiting...")
            ok = _wait_for_condition_locked(lambda: not _state.creating, timeout_s=10.0)

            # Creazione terminata o timeout
            if _state.instance is not None and not _is_adapter_closed(_state.instance):
                return _state.instance

            if not ok:
                raise MediaEngineError("Adapter creation did not complete (timeout)")

            if _state.last_error:
                raise MediaEngineError(f"Adapter creation failed: {_state.last_error}")

            raise MediaEngineError("Adapter creation did not produce an instance (unknown failure)")

        # Questa chiamata diventa la creator
        _state.creating = True
        _state.creator_tid = threading.get_ident()
        _state.last_error = None
        _state.creation_seq += 1
        seq = _state.creation_seq
        logger.info("[Factory] Creating adapter singleton (seq=%s)...", seq)

    # Creazione fuori lock (evita blocchi e deadlock)
    adapter: Any = None
    try:
        adapter = create_imf_media_engine_adapter(event_bus=event_bus)

        # Commit dello stato sotto lock, notifica i waiter
        with _adapter_lock:
            _state.instance = adapter
            _state.created = True
            _state.last_error = None

            # Prepara callback protetto: impedisce shutdown mentre callback è in esecuzione
            if _state.creation_callback is not None:
                _state.callback_running = True
                _state.callback_tid = threading.get_ident()
                cb_to_run = _state.creation_callback
                adapter_for_cb = adapter

            _state.creating = False
            _state.creator_tid = None
            _adapter_cv.notify_all()

        logger.info("[Factory] Adapter created successfully: %s (seq=%s)", type(adapter).__name__, seq)
        return adapter

    except FACTORY_IMPORT_EXCEPTIONS as exc:
        with _adapter_lock:
            _state.created = False
            _state.last_error = str(exc)
            _state.instance = None
            _state.creating = False
            _state.creator_tid = None
            _adapter_cv.notify_all()

        logger.error("[Factory] Failed to create adapter (seq=%s): %s", getattr(_state, "creation_seq", "?"), exc, exc_info=True)
        raise

    finally:
        # Callback fuori lock: evita re-entrancy/deadlock.
        if cb_to_run is not None:
            try:
                cb_to_run(adapter_for_cb)
            except FACTORY_STATE_EXCEPTIONS:
                logger.debug("[Factory] creation_callback failed (ignored)", exc_info=True)
            finally:
                with _adapter_lock:
                    _state.callback_running = False
                    _state.callback_tid = None
                    _adapter_cv.notify_all()


def get_video_adapter():
    """Alias: ritorna (e crea se necessario) l'adapter singleton."""
    return create_best_video_adapter()


def shutdown_video_adapter() -> None:
    """Esegue shutdown del singleton e lo rimuove dalla factory (best effort).

    - Attende la fine di una creazione in corso.
    - Evita di interrompere un callback in esecuzione (best effort).
    """
    adapter: Any = None

    with _adapter_lock:
        current_tid = threading.get_ident()

        # Attendi fine creazione (se in corso)
        if _state.creating and _state.creator_tid != current_tid:
            _wait_for_condition_locked(lambda: not _state.creating, timeout_s=5.0)

        # Attendi fine callback (se in corso) per evitare shutdown mentre callback usa l'istanza.
        if _state.callback_running and _state.callback_tid != current_tid:
            _wait_for_condition_locked(lambda: not _state.callback_running, timeout_s=5.0)

        adapter = _state.instance
        _state.instance = None
        _state.created = False

    if adapter is None:
        return

    try:
        if hasattr(adapter, "shutdown") and callable(getattr(adapter, "shutdown")):
            adapter.shutdown()
        elif hasattr(adapter, "close") and callable(getattr(adapter, "close")):
            adapter.close()
    except FACTORY_STATE_EXCEPTIONS as exc:
        logger.warning("[Factory] Adapter shutdown failed: %s", exc, exc_info=True)


def reset_adapter(event_bus: object | None = None) -> bool:
    """Reset robusto: shutdown + creazione nuova istanza singleton.

    Returns:
        True se reset riuscito, False altrimenti.
    """
    try:
        shutdown_video_adapter()
        create_best_video_adapter(event_bus=event_bus)
        return True
    except FACTORY_STATE_EXCEPTIONS as exc:
        logger.error("[Factory] Failed to reset adapter: %s", exc, exc_info=True)
        return False


__all__ = [
    "get_video_adapter",
    "create_imf_media_engine_adapter",
    "create_best_video_adapter",
    "shutdown_video_adapter",
    "is_adapter_created",
    "set_adapter_creation_callback",
    "get_adapter_status",
    "reset_adapter",
    "MediaEngineError",
]
