# -*- coding: utf-8 -*-
"""utils.py - utility generiche per il layer video/COM.

Questo modulo è deliberatamente piccolo:
- is_success(hr): verifica successo HRESULT
- safe_release(obj, name): rilascio best-effort per oggetti COM o ComPtr

Nota: non deve mai sollevare eccezioni durante cleanup/finalizer.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from typing import Any, Optional

from .com_helpers import ComPtr

logger = logging.getLogger(__name__)

ENV_PARSE_EXCEPTIONS = (AttributeError, TypeError, ValueError)
HRESULT_EXCEPTIONS = (AttributeError, TypeError, ValueError)
POINTER_EXCEPTIONS = (AttributeError, ctypes.ArgumentError, TypeError, ValueError)
RELEASE_EXCEPTIONS = POINTER_EXCEPTIONS + (ImportError, OSError, RuntimeError)
REGISTRY_SORT_EXCEPTIONS = (AttributeError, TypeError, ValueError)

# Registry best-effort per evitare doppi Release su puntatori già distrutti.
#
# Configurazione (override via env):
# - WAVEHELM_RELEASED_PTR_TTL_SEC: TTL in secondi (default 60.0). Imposta 0 per disabilitare la guardia.
# - WAVEHELM_RELEASED_PTR_PURGE_INTERVAL_SEC: frequenza minima di purge (default 5.0).
# - WAVEHELM_RELEASED_PTR_MAX_SIZE: limite massimo voci (default 8192).
#
# Motivazione:
# - Non possiamo garantire che un address non venga riusato dal sistema allocator; la guardia è best-effort.
# - Il purge ad ogni chiamata può essere costoso in carichi elevati: usiamo purge periodico/lazy.
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)).strip())
    except ENV_PARSE_EXCEPTIONS:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)).strip())
    except ENV_PARSE_EXCEPTIONS:
        return int(default)


_RELEASED_PTR_TTL_SEC = max(0.0, _env_float("WAVEHELM_RELEASED_PTR_TTL_SEC", 60.0))
_RELEASED_PTR_PURGE_INTERVAL_SEC = max(0.1, _env_float("WAVEHELM_RELEASED_PTR_PURGE_INTERVAL_SEC", 5.0))
_RELEASED_PTR_MAX_SIZE = max(256, _env_int("WAVEHELM_RELEASED_PTR_MAX_SIZE", 8192))

_released_ptrs_lock = threading.RLock()
_released_ptrs: dict[int, float] = {}
_last_purge_monotonic: float = 0.0


def is_success(hr: Any) -> bool:
    """True se HRESULT indica successo."""
    try:
        if hasattr(hr, "value"):
            hr_int = int(hr.value)
        else:
            hr_int = int(hr)
    except HRESULT_EXCEPTIONS:
        return False
    return (hr_int & 0x80000000) == 0


def _to_void_p(ptr: Any) -> Optional[ctypes.c_void_p]:
    """Normalizza un puntatore (ctypes pointer / c_void_p / int) in c_void_p.

    Returns:
        c_void_p con value != 0, oppure None se non convertibile/nullo.
    """
    if ptr is None:
        return None

    try:
        if isinstance(ptr, int):
            if ptr == 0:
                return None
            return ctypes.c_void_p(ptr)

        if isinstance(ptr, ctypes.c_void_p):
            if not ptr.value:
                return None
            # copia difensiva
            return ctypes.c_void_p(int(ptr.value))

        # ctypes POINTER(T) o simili
        void_p = ctypes.cast(ptr, ctypes.c_void_p)
        if not void_p.value:
            return None
        return ctypes.c_void_p(int(void_p.value))
    except POINTER_EXCEPTIONS:
        return None


def _maybe_purge_released_registry_locked(now: float) -> None:
    """Purge lazy del registry (chiamare solo con _released_ptrs_lock acquisito)."""
    global _last_purge_monotonic

    if not _released_ptrs:
        _last_purge_monotonic = now
        return

    # Evita purge troppo frequenti, a meno che la mappa non stia crescendo troppo.
    if (now - _last_purge_monotonic) < _RELEASED_PTR_PURGE_INTERVAL_SEC and len(_released_ptrs) < _RELEASED_PTR_MAX_SIZE:
        return

    if _RELEASED_PTR_TTL_SEC <= 0.0:
        # Guardia disabilitata: svuota per evitare crescita
        _released_ptrs.clear()
        _last_purge_monotonic = now
        return

    cutoff = now - _RELEASED_PTR_TTL_SEC
    expired = [addr for addr, ts in _released_ptrs.items() if ts < cutoff]
    for addr in expired:
        _released_ptrs.pop(addr, None)

    # Se ancora troppo grande, riduci mantenendo le entry più recenti.
    if len(_released_ptrs) > _RELEASED_PTR_MAX_SIZE:
        try:
            items = sorted(_released_ptrs.items(), key=lambda kv: kv[1], reverse=True)
            kept = dict(items[:_RELEASED_PTR_MAX_SIZE])
            dropped = len(_released_ptrs) - len(kept)
            _released_ptrs.clear()
            _released_ptrs.update(kept)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "Released-ptr registry trimmed (kept=%s dropped=%s ttl=%s interval=%s)",
                    len(kept),
                    dropped,
                    _RELEASED_PTR_TTL_SEC,
                    _RELEASED_PTR_PURGE_INTERVAL_SEC,
                )
        except REGISTRY_SORT_EXCEPTIONS as error:
            logger.debug("Released-ptr registry trim skipped: %s", error, exc_info=True)

    _last_purge_monotonic = now


def _mark_released(addr: int) -> None:
    if addr == 0:
        return
    now = time.monotonic()
    with _released_ptrs_lock:
        _maybe_purge_released_registry_locked(now)
        if _RELEASED_PTR_TTL_SEC <= 0.0:
            return
        _released_ptrs[int(addr)] = now

        # Se la mappa supera il limite, facciamo un purge extra (raro).
        if len(_released_ptrs) > _RELEASED_PTR_MAX_SIZE:
            _maybe_purge_released_registry_locked(now)


def _was_recently_released(addr: int) -> bool:
    if addr == 0:
        return False
    if _RELEASED_PTR_TTL_SEC <= 0.0:
        return False

    now = time.monotonic()
    with _released_ptrs_lock:
        _maybe_purge_released_registry_locked(now)
        ts = _released_ptrs.get(int(addr))
        if ts is None:
            return False
        return (now - ts) <= _RELEASED_PTR_TTL_SEC


def _try_release_iunknown(ptr: Any, name: str = "IUnknown") -> int:
    """Rilascia un puntatore COM (IUnknown*) best-effort.

    Miglioramenti rispetto alla versione precedente:
    - se Release() ritorna refcount=0, memorizza l'address per evitare doppi Release su memoria già liberata
      (guardia best-effort con TTL)
    - logga (a DEBUG) il refcount risultante quando disponibile
    - non deve mai sollevare eccezioni (finalizer/cleanup)

    Returns:
        refcount risultante (best effort), 0 se non disponibile o non rilasciato.
    """
    void_p = _to_void_p(ptr)
    if not void_p:
        return 0

    addr = int(void_p.value or 0)
    if addr == 0:
        return 0

    # Guardia contro doppi Release su puntatori che abbiamo già visto arrivare a rc=0.
    # Evita chiamate a vtable su memoria plausibilmente già liberata.
    if _was_recently_released(addr):
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("_try_release_iunknown(%s): skip double Release addr=0x%016X (recently released)", name, addr)
        return 0

    try:
        # Cast a IUnknown per chiamare Release
        from .definitions import IUnknown  # import locale per evitare cicli

        unk = ctypes.cast(void_p, ctypes.POINTER(IUnknown))
        if not unk or not getattr(unk, "contents", None) or not unk.contents.lpVtbl:
            return 0

        try:
            # "this" deve essere il puntatore all'interfaccia (IUnknown*)
            rc = unk.contents.lpVtbl.contents.Release(void_p)
            rc_int = int(rc) if rc is not None else 0

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("_try_release_iunknown(%s) rc=%s addr=0x%016X", name, rc_int, addr)

            # Se rc==0, l'oggetto è presumibilmente distrutto: segna l'address per evitare doppi Release.
            if rc_int == 0:
                _mark_released(addr)

            return rc_int

        except RELEASE_EXCEPTIONS as exc:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "_try_release_iunknown(%s) failed addr=0x%016X: %s",
                    name,
                    addr,
                    exc,
                    exc_info=True,
                )
            return 0

    except RELEASE_EXCEPTIONS:
        # best effort: mai propagare
        return 0


def safe_release(obj: Any, name: str = "object") -> None:
    """Rilascia in modo sicuro un oggetto/ptr COM o un ComPtr.

    Supporta:
    - ComPtr (release)
    - comtypes COMObject con Release()
    - ctypes pointer (POINTER(T)) o c_void_p / int (IUnknown*)
    """
    try:
        if obj is None:
            return

        # ComPtr: gestisce refcount e invalidazione internamente
        if isinstance(obj, ComPtr):
            try:
                obj.release()
            except RELEASE_EXCEPTIONS as exc:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("safe_release(%s): ComPtr.release failed: %s", name, exc, exc_info=True)
            return

        # comtypes / oggetti COM con Release
        if hasattr(obj, "Release") and callable(getattr(obj, "Release")):
            try:
                # comtypes Release() può non restituire refcount; best effort.
                obj.Release()
            except RELEASE_EXCEPTIONS as exc:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("safe_release(%s): Release() failed: %s", name, exc, exc_info=True)
            return

        # ctypes pointer / c_void_p / int
        _try_release_iunknown(obj, name=name)

        if logger.isEnabledFor(logging.DEBUG) and _to_void_p(obj) is None and not isinstance(obj, (int, ctypes.c_void_p, ComPtr)):
            logger.debug("safe_release(%s): unsupported object type %s", name, type(obj))

    except RELEASE_EXCEPTIONS as exc:
        logger.debug("safe_release(%s) raised: %s", name, exc, exc_info=True)


__all__ = [
    "is_success",
    "safe_release",
]
