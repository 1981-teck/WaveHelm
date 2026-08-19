# -*- coding: utf-8 -*-
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import POINTER, byref, c_uint32, c_void_p
from ctypes.wintypes import DWORD
from typing import Optional

from .definitions import (
    _mfplat,
    _ole32,
    GUID,
    HRESULT,
    IMFAttributes,
    IMFMediaEngineClassFactory,
    MFSTARTUP_FULL,
    MF_VERSION,
)
from .com_helpers import ComPtr, _check_hr, _hr_to_hex
from .iid_registry import (
    CLSID_MFMediaEngineClassFactory,
    IID_IMFMediaEngineClassFactory,
)

logger = logging.getLogger(__name__)

CLSCTX_INPROC_SERVER = 0x1

MF_BIND_EXCEPTIONS = (AttributeError, TypeError, ValueError)
MF_RUNTIME_EXCEPTIONS = (OSError, RuntimeError, TypeError, ValueError)


def _bind_export(
    dll: object,
    name: str,
    *,
    argtypes: list[object],
    restype: object,
) -> Optional[ctypes._CFuncPtr]:
    try:
        fn = getattr(dll, name)
        fn.argtypes = argtypes
        fn.restype = restype
        return fn
    except MF_BIND_EXCEPTIONS:
        return None


def _bind_export_any(
    dlls: tuple[object, ...] | list[object],
    name: str,
    *,
    argtypes: list[object],
    restype: object,
) -> Optional[ctypes._CFuncPtr]:
    for dll in dlls:
        fn = _bind_export(dll, name, argtypes=argtypes, restype=restype)
        if fn is not None:
            return fn
    return None


_MFStartup = _bind_export(_mfplat, "MFStartup", argtypes=[c_uint32, c_uint32], restype=HRESULT)
_MFShutdown = _bind_export(_mfplat, "MFShutdown", argtypes=[], restype=HRESULT)
_MFCreateAttributes = _bind_export(
    _mfplat,
    "MFCreateAttributes",
    argtypes=[POINTER(POINTER(IMFAttributes)), c_uint32],
    restype=HRESULT,
)
_MFCreateMediaEngineClassFactory = _bind_export_any(
    (_mfplat,),
    "MFCreateMediaEngineClassFactory",
    argtypes=[POINTER(POINTER(IMFMediaEngineClassFactory))],
    restype=HRESULT,
)

_CoCreateInstance = None
try:
    _CoCreateInstance = getattr(_ole32, "CoCreateInstance", None)
    if _CoCreateInstance is not None:
        _CoCreateInstance.argtypes = [POINTER(GUID), c_void_p, DWORD, POINTER(GUID), POINTER(c_void_p)]
        _CoCreateInstance.restype = HRESULT
except MF_BIND_EXCEPTIONS:
    _CoCreateInstance = None

_mf_lock = threading.RLock()
_mf_refcount: int = 0
_mf_started: bool = False


def MFStartup(flags: int = MFSTARTUP_FULL) -> None:
    global _mf_refcount, _mf_started

    if _MFStartup is None:
        raise RuntimeError("MFStartup non disponibile da mfplat.dll")

    with _mf_lock:
        if _mf_refcount > 0:
            _mf_refcount += 1
            _mf_started = True
            logger.debug("[MF] MFStartup già eseguito; incremento refcount=%s", _mf_refcount)
            return

        try:
            hr = _MFStartup(int(MF_VERSION), int(flags))
            _check_hr(hr, "MFStartup")
        except MF_RUNTIME_EXCEPTIONS:
            logger.exception("[MF] MFStartup fallito (flags=%s, version=0x%08X)", flags, int(MF_VERSION))
            raise

        _mf_refcount = 1
        _mf_started = True
        logger.info(
            "[MF] MFStartup ok (flags=%s, refcount=%s, version=0x%08X)",
            flags,
            _mf_refcount,
            int(MF_VERSION),
        )


def MFShutdown() -> None:
    global _mf_refcount, _mf_started

    with _mf_lock:
        if _mf_refcount <= 0:
            _mf_refcount = 0
            _mf_started = False
            return

        prev_refcount = _mf_refcount
        _mf_refcount -= 1
        if _mf_refcount > 0:
            _mf_started = True
            logger.debug("[MF] MFShutdown richiesto ma refcount>0; refcount=%s", _mf_refcount)
            return

        if _MFShutdown is None:
            _mf_refcount = prev_refcount
            _mf_started = True
            logger.warning(
                "[MF] MFShutdown non disponibile da mfplat.dll (skip). Ripristino refcount=%s",
                _mf_refcount,
            )
            return

        try:
            hr = _MFShutdown()
            _check_hr(hr, "MFShutdown")
        except MF_RUNTIME_EXCEPTIONS:
            _mf_refcount = prev_refcount
            _mf_started = True
            logger.exception(
                "[MF] MFShutdown fallito; ripristino refcount=%s (best effort)",
                _mf_refcount,
            )
            raise

        _mf_started = False
        _mf_refcount = 0
        logger.info("[MF] MFShutdown ok")


def MFCreateAttributes(initial_size: int = 8) -> ComPtr:
    if _MFCreateAttributes is None:
        raise RuntimeError("MFCreateAttributes non disponibile da mfplat.dll")

    p_attributes = POINTER(IMFAttributes)()
    hr = _MFCreateAttributes(byref(p_attributes), c_uint32(int(initial_size)))
    _check_hr(hr, "MFCreateAttributes")

    if not p_attributes:
        raise RuntimeError("MFCreateAttributes ha restituito un puntatore nullo")

    return ComPtr(p_attributes, IMFAttributes)


def _hresult_hex(hr: int) -> str:
    return _hr_to_hex(hr)


def _co_create_media_engine_class_factory_raw() -> POINTER(IMFMediaEngineClassFactory):
    if _CoCreateInstance is None:
        raise RuntimeError("CoCreateInstance non disponibile da ole32.dll")

    ppv = c_void_p()
    hr = _CoCreateInstance(
        byref(CLSID_MFMediaEngineClassFactory),
        None,
        DWORD(CLSCTX_INPROC_SERVER),
        byref(IID_IMFMediaEngineClassFactory),
        byref(ppv),
    )
    _check_hr(hr, "CoCreateInstance(IMFMediaEngineClassFactory)")

    if not ppv.value:
        raise RuntimeError("CoCreateInstance ha restituito ppv nullo")

    return ctypes.cast(ctypes.c_void_p(ppv.value), POINTER(IMFMediaEngineClassFactory))


def MFCreateMediaEngine() -> ComPtr:
    if _MFCreateMediaEngineClassFactory is not None:
        p_factory = POINTER(IMFMediaEngineClassFactory)()
        hr = _MFCreateMediaEngineClassFactory(byref(p_factory))
        _check_hr(hr, "MFCreateMediaEngineClassFactory")
        if not p_factory:
            raise RuntimeError("MFCreateMediaEngineClassFactory ha restituito un puntatore nullo")
        logger.info("[MF] IMFMediaEngineClassFactory creata via export mfplat")
        return ComPtr(p_factory, IMFMediaEngineClassFactory)

    p_factory = _co_create_media_engine_class_factory_raw()
    logger.info("[MF] IMFMediaEngineClassFactory creata via CoCreateInstance")
    return ComPtr(p_factory, IMFMediaEngineClassFactory)


def MFCreateMediaEngineClassFactory() -> ComPtr:
    return MFCreateMediaEngine()


__all__ = [
    "MFStartup",
    "MFShutdown",
    "MFCreateAttributes",
    "MFCreateMediaEngine",
    "MFCreateMediaEngineClassFactory",
    "CLSID_MFMediaEngineClassFactory",
    "IID_IMFMediaEngineClassFactory",
    "CLSCTX_INPROC_SERVER",
]
