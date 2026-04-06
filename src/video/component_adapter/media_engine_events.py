# -*- coding: utf-8 -*-
"""media_engine_events.py
Gestione callback eventi per IMFMediaEngine (IMFMediaEngineNotify).

Obiettivi:
- Non propagare eccezioni verso il motore (stabilità).
- Evitare reference cycle (weakref verso l'adapter).
- Supportare comtypes quando disponibile, e fallback puro ctypes quando non lo è.

MediaEngineCore usa:
- _MediaEngineNotifyCOM(adapter)
- notify_handler.QueryInterface(...)-> oggetto passabile a IMFAttributes::SetUnknown(...)
- shutdown() chiama notify_iunknown.Release() (best effort)

Quindi, nel fallback ctypes, QueryInterface() restituisce un wrapper con:
- _as_parameter_ (per ctypes)
- Release() (per teardown pulito)
"""

from __future__ import annotations

import ctypes
import threading
import weakref
from ctypes import wintypes

from src.video.mf_base import S_OK, logger

COERCE_INT_EXCEPTIONS = (TypeError, ValueError, OverflowError)
READ_POINTER_EXCEPTIONS = (OSError, TypeError, ValueError)
IID_EXTRACTION_EXCEPTIONS = (AttributeError, OSError, OverflowError, TypeError, ValueError)
NOTIFY_CALLBACK_EXCEPTIONS = (AttributeError, OSError, ReferenceError, RuntimeError, TypeError, ValueError, ctypes.ArgumentError)
INTERLOCKED_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError, ctypes.ArgumentError)
from src.video.component_base.definitions import (
    _COMTYPES_AVAILABLE,
    COMObject,
    IMFMediaEngineNotify,
)
from src.video.component_base.definitions_runtime import _bind_function, _load_windll
from src.video.component_base.win_types import DWORD_PTR

# HRESULT standard (fallback; se mf_base li definisce già, i valori coincidono)
E_NOINTERFACE = 0x80004002
E_POINTER = 0x80004003

# WINFUNCTYPE è disponibile solo su Windows.
# Fuori Windows usiamo CFUNCTYPE solo per evitare crash a import-time/test collection;
# il runtime reale dell'app resta Windows-only.
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


def _safe_int(value) -> int:
    """Conversione robusta a int per parametri COM (best effort)."""
    try:
        return int(value)
    except COERCE_INT_EXCEPTIONS:
        return 0


def _safe_read_16bytes(ptr_val: int) -> bytes | None:
    """Legge 16 byte da un puntatore (best effort).

    In caso di puntatore nullo o non leggibile, ritorna None (non solleva).
    """
    if not ptr_val:
        return None
    try:
        return bytes(ctypes.string_at(ctypes.c_void_p(ptr_val), 16))
    except READ_POINTER_EXCEPTIONS:
        return None


# IUnknown IID (00000000-0000-0000-C000-000000000046) in layout memoria GUID Win32.
_IID_IUNKNOWN_BYTES = bytes.fromhex(
    "00000000"  # Data1
    "0000"      # Data2
    "0000"      # Data3
    "C000"      # Data4[0..1]
    "000000000046"  # Data4[2..7]
)


def _iid_bytes_from_iface(iface) -> bytes | None:
    """Estrae l'IUnknown IID in bytes da una interface class/obj (best effort)."""
    iid = getattr(iface, "_iid_", None)
    if iid is None:
        iid = getattr(iface, "IID", None)
    if iid is None:
        return None

    # Se è un oggetto GUID con campi Data1/Data2/Data3/Data4.
    try:
        data1 = int(getattr(iid, "Data1"))
        data2 = int(getattr(iid, "Data2"))
        data3 = int(getattr(iid, "Data3"))
        data4 = getattr(iid, "Data4")
        if isinstance(data4, (bytes, bytearray)):
            d4 = bytes(data4)
        else:
            d4 = bytes(int(x) & 0xFF for x in data4)
        if len(d4) != 8:
            return None
        return (
            data1.to_bytes(4, "little")
            + data2.to_bytes(2, "little")
            + data3.to_bytes(2, "little")
            + d4
        )
    except IID_EXTRACTION_EXCEPTIONS as error:
        logger.debug("IID object extraction fallback for %r: %s", iid, error, exc_info=True)

    # Se è stringa "{XXXXXXXX-....}"
    try:
        s = str(iid).strip()
        if s.startswith("{") and s.endswith("}"):
            s = s[1:-1]
        parts = s.split("-")
        if len(parts) != 5:
            return None
        data1 = int(parts[0], 16)
        data2 = int(parts[1], 16)
        data3 = int(parts[2], 16)
        data4 = bytes.fromhex(parts[3] + parts[4])
        if len(data4) != 8:
            return None
        return (
            data1.to_bytes(4, "little")
            + data2.to_bytes(2, "little")
            + data3.to_bytes(2, "little")
            + data4
        )
    except IID_EXTRACTION_EXCEPTIONS:
        return None


_IID_IMF_MEDIA_ENGINE_NOTIFY_BYTES = _iid_bytes_from_iface(IMFMediaEngineNotify)


if _COMTYPES_AVAILABLE:
    # ------------------------------------------------------------------
    # Implementazione basata su comtypes
    # ------------------------------------------------------------------

    class _MediaEngineNotifyCOM(COMObject):
        """Callback COM per IMFMediaEngineNotify (comtypes)."""

        _com_interfaces_ = [IMFMediaEngineNotify]

        def __init__(self, adapter) -> None:
            super().__init__()
            self._adapter_ref = weakref.ref(adapter)

        def EventNotify(self, meEvent, param1, param2):  # noqa: N802 (nome COM)
            adapter = self._adapter_ref()
            if adapter is not None:
                try:
                    adapter.on_media_engine_event(_safe_int(meEvent), _safe_int(param1), _safe_int(param2))
                except (KeyboardInterrupt, SystemExit):
                    raise
                except NOTIFY_CALLBACK_EXCEPTIONS as exc:
                    logger.exception(
                        "[_MediaEngineNotifyCOM] Errore durante la gestione evento: %s",
                        exc,
                    )
            # IMPORTANTE: restituire un int puro (evita warning/conversioni)
            return int(S_OK)

else:
    # ------------------------------------------------------------------
    # Fallback puro ctypes (nessuna dipendenza comtypes)
    # ------------------------------------------------------------------

    HRESULT = ctypes.c_long
    ULONG = ctypes.c_ulong

    QI_PROTO = _WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
    ADDREF_PROTO = _WINFUNCTYPE(ULONG, ctypes.c_void_p)
    RELEASE_PROTO = _WINFUNCTYPE(ULONG, ctypes.c_void_p)
    EVENT_PROTO = _WINFUNCTYPE(HRESULT, ctypes.c_void_p, wintypes.DWORD, DWORD_PTR, wintypes.DWORD)

    _kernel32 = _load_windll("kernel32")
    _InterlockedIncrement = _bind_function(
        _kernel32,
        "kernel32",
        "InterlockedIncrement",
        argtypes=[ctypes.POINTER(ctypes.c_long)],
        restype=ctypes.c_long,
    )
    _InterlockedDecrement = _bind_function(
        _kernel32,
        "kernel32",
        "InterlockedDecrement",
        argtypes=[ctypes.POINTER(ctypes.c_long)],
        restype=ctypes.c_long,
    )

    class _IMFMediaEngineNotifyVtbl(ctypes.Structure):
        _fields_ = [
            ("QueryInterface", ctypes.c_void_p),
            ("AddRef", ctypes.c_void_p),
            ("Release", ctypes.c_void_p),
            ("EventNotify", ctypes.c_void_p),
        ]

    class _IUnknownRef:
        """Wrapper minimale per un puntatore IUnknown.

        - _as_parameter_: usabile come argomento ctypes (c_void_p)
        - Release(): usato da MediaEngineCore durante shutdown
        """

        __slots__ = ("_ptr", "_owner")

        def __init__(self, ptr: int, owner: "_MediaEngineNotifyCOM") -> None:
            self._ptr = int(ptr)
            self._owner = owner  # mantiene viva la callback

        @property
        def _as_parameter_(self):  # noqa: N802 (ctypes convention)
            return ctypes.c_void_p(self._ptr)

        def Release(self):  # noqa: N802 (COM convention)
            return self._owner._release_impl(ctypes.c_void_p(self._ptr).value)

        def __repr__(self) -> str:
            return f"<IUnknownRef 0x{self._ptr:016X}>"

    class _MediaEngineNotifyCOM(ctypes.Structure):
        """Callback COM (ctypes) per IMFMediaEngineNotify."""

        _fields_ = [
            ("lpVtbl", ctypes.POINTER(_IMFMediaEngineNotifyVtbl)),
            ("_refcount", ctypes.c_long),
            ("_adapter_ref", ctypes.py_object),
            ("_lock", ctypes.py_object),
        ]

        _vtbl_singleton: _IMFMediaEngineNotifyVtbl | None = None
        _cb_qi = None
        _cb_addref = None
        _cb_release = None
        _cb_event = None

        def __init__(self, adapter) -> None:
            super().__init__()
            self._refcount = ctypes.c_long(1)
            self._adapter_ref = weakref.ref(adapter)
            self._lock = threading.RLock()

            if _MediaEngineNotifyCOM._vtbl_singleton is None:
                _MediaEngineNotifyCOM._install_vtbl()

            self.lpVtbl = ctypes.pointer(_MediaEngineNotifyCOM._vtbl_singleton)  # type: ignore[arg-type]

        @classmethod
        def _install_vtbl(cls) -> None:
            def _qi(this, riid, ppv):
                try:
                    if not ppv:
                        return int(E_POINTER)
                    ppv[0] = None

                    iid_bytes = _safe_read_16bytes(int(riid) if riid else 0)
                    if iid_bytes is None:
                        return int(E_NOINTERFACE)

                    if iid_bytes == _IID_IUNKNOWN_BYTES:
                        ppv[0] = this
                        cls._addref_impl(this)
                        return int(S_OK)

                    if _IID_IMF_MEDIA_ENGINE_NOTIFY_BYTES and iid_bytes == _IID_IMF_MEDIA_ENGINE_NOTIFY_BYTES:
                        ppv[0] = this
                        cls._addref_impl(this)
                        return int(S_OK)

                    return int(E_NOINTERFACE)
                except NOTIFY_CALLBACK_EXCEPTIONS:
                    return int(E_NOINTERFACE)

            def _addref(this):
                return int(cls._addref_impl(this))

            def _release(this):
                return int(cls._release_impl(this))

            def _event(this, meEvent, param1, param2):
                try:
                    obj = ctypes.cast(this, ctypes.POINTER(cls)).contents
                    adapter = obj._adapter_ref() if obj._adapter_ref else None
                    if adapter is not None:
                        try:
                            adapter.on_media_engine_event(_safe_int(meEvent), _safe_int(param1), _safe_int(param2))
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except NOTIFY_CALLBACK_EXCEPTIONS as exc:
                            logger.exception("[_MediaEngineNotifyCOM(ctypes)] errore evento: %s", exc)
                    return int(S_OK)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except NOTIFY_CALLBACK_EXCEPTIONS:
                    return int(S_OK)

            cls._cb_qi = QI_PROTO(_qi)
            cls._cb_addref = ADDREF_PROTO(_addref)
            cls._cb_release = RELEASE_PROTO(_release)
            cls._cb_event = EVENT_PROTO(_event)

            cls._vtbl_singleton = _IMFMediaEngineNotifyVtbl(
                ctypes.cast(cls._cb_qi, ctypes.c_void_p).value,
                ctypes.cast(cls._cb_addref, ctypes.c_void_p).value,
                ctypes.cast(cls._cb_release, ctypes.c_void_p).value,
                ctypes.cast(cls._cb_event, ctypes.c_void_p).value,
            )

        @classmethod
        def _addref_impl(cls, this) -> int:
            try:
                obj = ctypes.cast(this, ctypes.POINTER(cls)).contents
                return int(_InterlockedIncrement(ctypes.byref(obj._refcount)))
            except INTERLOCKED_EXCEPTIONS:
                return 1

        @classmethod
        def _release_impl(cls, this) -> int:
            try:
                obj = ctypes.cast(this, ctypes.POINTER(cls)).contents
                return int(_InterlockedDecrement(ctypes.byref(obj._refcount)))
            except INTERLOCKED_EXCEPTIONS:
                return 0

        def EventNotify(self, meEvent, param1, param2):  # noqa: N802 (nome COM)
            adapter = self._adapter_ref() if self._adapter_ref else None
            if adapter is not None:
                try:
                    adapter.on_media_engine_event(_safe_int(meEvent), _safe_int(param1), _safe_int(param2))
                except (KeyboardInterrupt, SystemExit):
                    raise
                except NOTIFY_CALLBACK_EXCEPTIONS as exc:
                    logger.exception("[_MediaEngineNotifyCOM(ctypes)] errore evento: %s", exc)
            return int(S_OK)

        def QueryInterface(self, _iface=None):  # compat MediaEngineCore
            """Ritorna wrapper IUnknown passabile a SetUnknown e con Release()."""
            ptr = ctypes.addressof(self)
            return _IUnknownRef(ptr, self)

        def __bool__(self) -> bool:
            return True
