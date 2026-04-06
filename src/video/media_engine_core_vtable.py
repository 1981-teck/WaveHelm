from __future__ import annotations

import ctypes
import logging
import math
from ctypes import POINTER, c_double, c_void_p, cast
from typing import Any, Optional

from src.video.component_base.com_helpers import _hr_to_hex
from src.video.component_base.definitions import IUnknown, IMFMediaEngine, _IMF_MEDIA_ENGINE_VTBL_SPECS
from src.video.component_base.utils import is_success, safe_release

from .media_engine_core_shared import MediaEngineError

logger = logging.getLogger(__name__)

MEDIA_ENGINE_PTR_CALL_EXCEPTIONS = (
    MediaEngineError,
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)
MEDIA_ENGINE_NOTIFY_EXCEPTIONS = (
    AttributeError,
    TypeError,
    ValueError,
)
MEDIA_ENGINE_FLOAT_EXCEPTIONS = (TypeError, ValueError, OverflowError)


def _call_vtable_method(self, method_name: str, *args):
    with self._state_lock:
        engine = self._media_engine

    if not engine:
        raise MediaEngineError(f"Impossibile chiamare {method_name}: MediaEngine non inizializzato.")

    spec = _IMF_MEDIA_ENGINE_VTBL_SPECS.get(method_name)
    if spec is None:
        vtbl = engine.contents.lpVtbl.contents
        fn = getattr(vtbl, method_name, None)
        if fn is None:
            raise MediaEngineError(f"Metodo {method_name} non presente nella vtable/struct.")
        if not isinstance(fn, ctypes._CFuncPtr):
            raise MediaEngineError(f"Metodo {method_name} presente ma non è una funzione chiamabile ctypes (fallback rifiutato).")

        argtypes = getattr(fn, "argtypes", None)
        if not argtypes:
            raise MediaEngineError(
                f"Fallback rifiutato per {method_name}: argtypes non disponibili sulla funzione in lpVtbl. "
                f"Soluzione: aggiungi/correggi {method_name} in _IMF_MEDIA_ENGINE_VTBL_SPECS (definitions.py) "
                f"con index/restype/argtypes corretti (derivati dagli header IMFMediaEngine)."
            )

        expected = 1 + len(args)
        if len(argtypes) != expected:
            raise MediaEngineError(
                f"Fallback rifiutato per {method_name}: signature mismatch. "
                f"Attesi {expected} argomenti (this+args), trovati {len(argtypes)} in fn.argtypes. "
                f"Soluzione: aggiungi/correggi {method_name} in _IMF_MEDIA_ENGINE_VTBL_SPECS (definitions.py) "
                f"oppure imposta argtypes/restype corretti nel campo vtable."
            )

        logger.debug("[MediaEngineCore] Uso fallback lpVtbl per %s (spec mancante).", method_name)
        return fn(engine, *args)

    index = int(spec["index"])
    restype = spec["restype"]
    argtypes = spec["argtypes"]

    cache_key = (index, restype, tuple(argtypes))
    fn = self._vtable_call_cache.get(cache_key)
    if fn is None:
        vtbl_ptr = cast(engine.contents.lpVtbl, POINTER(c_void_p))
        addr = vtbl_ptr[index]
        fn = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(addr)
        self._vtable_call_cache[cache_key] = fn

    return fn(cast(engine, c_void_p), *args)


def _call_engine_ptr_method(
    engine_ptr: Optional[ctypes.POINTER(IMFMediaEngine)],
    method_name: str,
    *args: Any,
) -> Any:
    if not engine_ptr:
        raise MediaEngineError(f"Metodo {method_name} non invocabile: engine_ptr assente.")

    spec = _IMF_MEDIA_ENGINE_VTBL_SPECS.get(method_name)
    if spec is None:
        raise MediaEngineError(f"Spec vtable mancante per {method_name}.")

    vtbl_ptr = cast(engine_ptr.contents.lpVtbl, POINTER(c_void_p))
    addr = vtbl_ptr[int(spec["index"])]
    fn = ctypes.WINFUNCTYPE(spec["restype"], c_void_p, *spec["argtypes"])(addr)
    return fn(cast(engine_ptr, c_void_p), *args)


def _stop_engine_ptr_playback(self, engine_ptr: Optional[ctypes.POINTER(IMFMediaEngine)]) -> None:
    if not engine_ptr:
        return

    try:
        self._call_engine_ptr_method(engine_ptr, "Pause")
    except MEDIA_ENGINE_PTR_CALL_EXCEPTIONS:
        logger.debug("[MediaEngineCore] Pause during stop failed.", exc_info=True)

    try:
        self._call_engine_ptr_method(engine_ptr, "SetCurrentTime", c_double(0.0))
    except MEDIA_ENGINE_PTR_CALL_EXCEPTIONS:
        logger.debug("[MediaEngineCore] SetCurrentTime(0) during stop failed.", exc_info=True)


def _shutdown_engine_ptr(self, engine_ptr: Optional[ctypes.POINTER(IMFMediaEngine)]) -> None:
    if not engine_ptr:
        return

    try:
        hr = int(self._call_engine_ptr_method(engine_ptr, "Shutdown"))
    except MEDIA_ENGINE_PTR_CALL_EXCEPTIONS:
        logger.debug("[MediaEngineCore] IMFMediaEngine::Shutdown failed.", exc_info=True)
        return

    if not is_success(hr):
        logger.debug("[MediaEngineCore] IMFMediaEngine::Shutdown returned hr=%s", _hr_to_hex(hr))


def _release_notify_iunknown(notify_iunknown: Optional[Any]) -> None:
    if notify_iunknown is None:
        return

    try:
        safe_release(notify_iunknown, "notify_iunknown")
    finally:
        try:
            if type(notify_iunknown).__module__.startswith("comtypes"):
                ctypes.c_void_p.value.__set__(notify_iunknown, None)
        except MEDIA_ENGINE_NOTIFY_EXCEPTIONS:
            logger.debug("[MediaEngineCore] Failed detaching comtypes notify pointer.", exc_info=True)


def _sanitize_nonneg_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
    except MEDIA_ENGINE_FLOAT_EXCEPTIONS:
        return float(default)
    if not math.isfinite(f) or f < 0.0:
        return float(default)
    return f


def get_requested_source(self) -> Optional[str]:
    with self._state_lock:
        return self._requested_source


def get_active_source(self) -> Optional[str]:
    with self._state_lock:
        return self._active_source


_MEDIA_ENGINE_CORE_VTABLE_METHODS = (
    ("_call_vtable_method", _call_vtable_method),
    ("_call_engine_ptr_method", staticmethod(_call_engine_ptr_method)),
    ("_stop_engine_ptr_playback", _stop_engine_ptr_playback),
    ("_shutdown_engine_ptr", _shutdown_engine_ptr),
    ("_release_notify_iunknown", staticmethod(_release_notify_iunknown)),
    ("_sanitize_nonneg_float", staticmethod(_sanitize_nonneg_float)),
    ("get_requested_source", get_requested_source),
    ("get_active_source", get_active_source),
)


def install_media_engine_core_vtable_behavior(cls) -> None:
    """Install MediaEngineCore vtable behavior on the central class.

    Edge cases:
        - Re-running the installer during reloads can silently replace cached call helpers.
        - Partial split imports can leave pointer helpers installed without the related source accessors.
        - Legacy leaf entrypoints can drift from the central vtable contract over time.
    """
    if getattr(cls, "_media_engine_core_vtable_behavior_attached", False):
        return

    for name, method in _MEDIA_ENGINE_CORE_VTABLE_METHODS:
        setattr(cls, name, method)

    setattr(cls, "_media_engine_core_vtable_behavior_attached", True)


def attach_media_engine_core_vtable_behavior(cls) -> None:
    """Compatibility shim for historical attach_* imports.

    Prefer install_media_engine_core_vtable_behavior() from the central coordinator path.
    """
    install_media_engine_core_vtable_behavior(cls)
