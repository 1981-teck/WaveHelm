# -*- coding: utf-8 -*-
"""com_helpers.py - Utilità generiche per la gestione COM.

Obiettivi:
- fornire una classe ComPtr minimale e robusta per gestire puntatori COM
- fornire _check_hr / _hr_to_hex per logging e validazione HRESULT

Note progettuali:
- COM è intrinsecamente sensibile al thread: questa classe rende thread-safe *solo*
  l'accesso al campo puntatore (_ptr) per evitare race condition su attach/release/cast.
  Non rende "thread-safe" l'oggetto COM sottostante (che tipicamente richiede STA/MTA).
"""

from __future__ import annotations

import ctypes as _ctypes
import logging
import sys as _sys
from dataclasses import dataclass as _dataclass
from enum import Enum as _Enum
from typing import NamedTuple as _NamedTuple


class _CtypesProxy:
    def __getattr__(self, name: str):
        return getattr(_ctypes, name)


ctypes = _CtypesProxy()

if hasattr(_ctypes, "WinDLL"):
    ctypes.WinDLL = _ctypes.WinDLL
else:
    def _missing_windll(*args, **kwargs):
        raise OSError("ctypes.WinDLL non disponibile su questa piattaforma")

    ctypes.WinDLL = _missing_windll

import threading
from typing import Any, Optional, Type

from .definitions import HRESULT, S_OK, GUID, IUnknown

logger = logging.getLogger(__name__)

HRESULT_COERCE_EXCEPTIONS = (TypeError, ValueError, OverflowError)
SYSTEM_MESSAGE_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError, ctypes.ArgumentError)
POINTER_COERCE_EXCEPTIONS = (TypeError, ValueError, ctypes.ArgumentError)
COM_PTR_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError, ctypes.ArgumentError)

# Mappa essenziale di HRESULT comuni (unsigned 32-bit)
HRESULTS: dict[int, str] = {
    int(S_OK) & 0xFFFFFFFF: "S_OK",
    0x80004002: "E_NOINTERFACE",
    0x80004005: "E_FAIL",
    0x80070057: "E_INVALIDARG",
    0x8007000E: "E_OUTOFMEMORY",
    0x80004003: "E_POINTER",
    0x8000FFFF: "E_UNEXPECTED",
    0x8001010D: "RPC_E_CANTCALLOUT_ININPUTSYNCCALL",
    0x8001011A: "RPC_S_CALLPENDING",
    0xC00D5212: "MF_E_TOPO_CODEC_NOT_FOUND",
}


def _hr_to_uint32(hr: Any) -> int:
    """Normalizza un HRESULT (ctypes o int) in uint32."""
    try:
        if hasattr(hr, "value"):
            hr = int(hr.value)
        else:
            hr = int(hr)
    except HRESULT_COERCE_EXCEPTIONS:
        hr = 0
    return hr & 0xFFFFFFFF


def _hr_to_hex(hr: Any) -> str:
    """Ritorna l'HRESULT in formato esadecimale 0xXXXXXXXX."""
    return f"0x{_hr_to_uint32(hr):08X}"


def _format_hr(hr: Any) -> str:
    hr_u = _hr_to_uint32(hr)
    name = HRESULTS.get(hr_u)
    return f"{_hr_to_hex(hr_u)}" + (f" ({name})" if name else "")


def get_hresult_system_message(hr: Any) -> str:
    """Restituisce il messaggio di sistema associato a un HRESULT, se disponibile."""
    hr_u = _hr_to_uint32(hr)
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        format_message = kernel32.FormatMessageW
        format_message.argtypes = [
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        format_message.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(2048)
        flags = 0x00001000 | 0x00000200  # FORMAT_MESSAGE_FROM_SYSTEM | IGNORE_INSERTS
        count = int(format_message(flags, None, hr_u, 0, buffer, len(buffer), None))
        if count <= 0:
            return ""
        return str(buffer.value or "").strip().rstrip(".")
    except SYSTEM_MESSAGE_EXCEPTIONS:
        logger.debug("Failed resolving HRESULT system message for %s", _hr_to_hex(hr_u), exc_info=True)
        return ""


def describe_hresult(hr: Any) -> str:
    """Restituisce una descrizione umana di un HRESULT."""
    base = _format_hr(hr)
    system_message = get_hresult_system_message(hr)
    if system_message:
        return f"{base}: {system_message}"
    return base


def _check_hr(hr: Any, context: str = "COM call") -> None:
    """Solleva RuntimeError se hr indica fallimento."""
    hr_u = _hr_to_uint32(hr)
    if (hr_u & 0x80000000) != 0:
        raise RuntimeError(f"{context} failed: hr={_format_hr(hr_u)}")


def _coerce_ptr_value(ptr: Any) -> int:
    """Estrae il valore address (int) da vari tipi di puntatore/ctypes."""
    if ptr is None:
        return 0

    # c_void_p
    if isinstance(ptr, ctypes.c_void_p):
        return int(ptr.value or 0)

    # int address
    if isinstance(ptr, int):
        return int(ptr)

    # ctypes pointer (POINTER(T))
    try:
        return int(ctypes.cast(ptr, ctypes.c_void_p).value or 0)
    except POINTER_COERCE_EXCEPTIONS:
        return 0


class _ReleaseState(_Enum):
    PREPARING = "preparing"
    DISPATCHED = "dispatched"
    RELEASED = "released"
    NOT_DISPATCHED = "not_dispatched"
    HELD = "held_not_dispatched"
    UNCERTAIN = "uncertain"
    TRANSFERRED = "transferred"


class _ReleaseSnapshot(_NamedTuple):
    address: int
    generation: int
    state: _ReleaseState
    error: BaseException | None
    refcount: int | None


@_dataclass(slots=True)
class _ReleaseAttempt:
    pointer: _ctypes.c_void_p | None = None
    address: int = 0
    generation: int = 0
    state: _ReleaseState = _ReleaseState.PREPARING
    error: BaseException | None = None
    refcount: int | None = None


class _ComPtrBusyError(RuntimeError):
    """Ownership cannot be consumed while a prior release remains unresolved."""


class ComPtr:
    """RAII leggero per un puntatore COM.

    Nota: questa classe NON fa QueryInterface automaticamente; serve solo per:
    - conservare/rilasciare un puntatore
    - castarlo in una POINTER(interface_type) quando richiesto
    """

    __slots__ = ("_ptr", "_iface_type", "_lock", "_gen", "_release_attempt")

    def __init__(self, ptr: Any = None, iface_type: Optional[Type[Any]] = None) -> None:
        self._ptr: Optional[ctypes.c_void_p] = None
        self._iface_type: Optional[Type[Any]] = iface_type
        self._lock = threading.RLock()
        self._gen: int = 0
        self._release_attempt: _ReleaseAttempt | None = None

        if ptr is not None:
            self.attach(ptr, iface_type=iface_type)

    def __bool__(self) -> bool:
        with self._lock:
            return bool(self._ptr and self._ptr.value)

    def __repr__(self) -> str:
        with self._lock:
            addr = int(self._ptr.value) if (self._ptr and self._ptr.value) else 0
            tname = getattr(self._iface_type, "__name__", None) if self._iface_type else None
        return f"ComPtr(ptr=0x{addr:016X}, type={tname})"

    @property
    def ptr(self) -> Optional[ctypes.c_void_p]:
        """Puntatore grezzo (c_void_p) o None.

        Ritorna una *copia* del c_void_p per evitare che codice esterno modifichi
        accidentalmente self._ptr.value.
        """
        with self._lock:
            if not self._ptr or not self._ptr.value:
                return None
            return ctypes.c_void_p(int(self._ptr.value))

    def _get_ptr_value_locked(self) -> int:
        return int(self._ptr.value) if (self._ptr and self._ptr.value) else 0

    def _bump_gen_locked(self) -> None:
        """Incrementa un contatore di generazione per distinguere cambi di stato.

        Serve per evitare invalidazioni errate in race rare (es. riuso dello stesso address).
        """
        self._gen = (self._gen + 1) & 0x7FFFFFFF

    def attach(self, ptr: Any, iface_type: Optional[Type[Any]] = None) -> None:
        """Transfer a pointer; same-address attachment keeps the current ownership.

        Replacement is published atomically before releasing the previous reference.
        A rejected busy transfer leaves the incoming ownership with the caller.
        """
        attempt = self._exchange_pointer(ptr, iface_type, keep_same=True)
        if attempt is not None:
            self._run_release(attempt)


    def adopt(self, ptr: Any, iface_type: Optional[Type[Any]] = None) -> None:
        """Transfer a NEW reference, even at the same address; never infer AddRef.

        Only pass a separately acquired reference. A borrowed cast is not ownership.
        Failed old-reference cleanup does not undo an already admitted replacement.
        """
        attempt = self._exchange_pointer(ptr, iface_type, keep_same=False)
        if attempt is not None:
            self._run_release(attempt)

    def detach(self) -> Optional[ctypes.c_void_p]:
        """Ritorna il puntatore e disabilita il rilascio automatico."""
        with self._lock:
            p = self._ptr
            self._ptr = None
            self._bump_gen_locked()
            if not p or not p.value:
                return None
            # Restituisce una copia per coerenza
            return ctypes.c_void_p(int(p.value))

    def as_interface(self, iface_type: Type[Any]) -> Optional[Any]:
        """Ritorna POINTER(iface_type) oppure None."""
        with self._lock:
            val = self._get_ptr_value_locked()
        if not val:
            return None
        return ctypes.cast(ctypes.c_void_p(val), ctypes.POINTER(iface_type))

    def as_iunknown(self) -> Optional[Any]:
        """Ritorna POINTER(IUnknown) oppure None."""
        return self.as_interface(IUnknown)

    def add_ref(self) -> int:
        """Incrementa il refcount (IUnknown::AddRef) e ritorna il refcount risultante (best effort)."""
        with self._lock:
            val = self._get_ptr_value_locked()
        if not val:
            return 0

        try:
            iunk = ctypes.cast(ctypes.c_void_p(val), ctypes.POINTER(IUnknown))
            if iunk and iunk.contents and iunk.contents.lpVtbl:
                rc = iunk.contents.lpVtbl.contents.AddRef(ctypes.cast(iunk, ctypes.c_void_p))
                return int(rc) if rc is not None else 0
        except COM_PTR_EXCEPTIONS as exc:
            logger.debug("Exception in ComPtr.add_ref(): %s", exc, exc_info=True)
        return 0

    def release(self) -> int:
        """Consume this ownership before dispatch, without locking foreign calls.

        Duplicate entry while the slot is empty performs no call. Pre-dispatch
        failures retain ownership; uncertain dispatches are never retried.
        The legacy integer result is not a completion receipt: inspect release_snapshot.
        """
        attempt = self._exchange_pointer(None, None, keep_same=False, releasing=True)
        return 0 if attempt is None else self._run_release(attempt)

    @property
    def release_snapshot(self) -> _ReleaseSnapshot | None:
        """Return the latest attempt's immutable metadata and exact Python error.

        One receipt is retained, not an unbounded journal. A zero return from
        release()/safe_release() alone does not establish successful cleanup.
        """
        with self._lock:
            item = self._release_attempt
            if item is None:
                return None
            values = (item.address, item.generation, item.state, item.error, item.refcount)
        return _ReleaseSnapshot(*values)

    def _exchange_pointer(self, ptr: object, iface_type: type[object] | None, *,
                          keep_same: bool, releasing: bool = False) -> _ReleaseAttempt | None:
        """Atomically exchange a live slot and reserve at most one old release.

        Reentry with no replacement is a no-op. A preparing/uncertain attempt cannot
        be displaced. Replacement during dispatch is allowed only into an empty slot.
        No cast, allocation of pointer storage, logging or foreign call holds the lock.
        """
        new_val = _coerce_ptr_value(ptr)
        replacement = ctypes.c_void_p(new_val) if new_val else None
        attempt = _ReleaseAttempt()
        with self._lock:
            prior = self._release_attempt  # Keep prior exception/finalizer destruction unlocked.
            cur_val = self._get_ptr_value_locked()
            if prior is not None and prior.state in (_ReleaseState.HELD, _ReleaseState.UNCERTAIN):
                raise _ComPtrBusyError("Prior release requires explicit ownership review")
            if keep_same and new_val and new_val == cur_val:
                if iface_type is not None:
                    self._iface_type = iface_type
                return None
            if prior is not None and prior.state in (_ReleaseState.PREPARING, _ReleaseState.DISPATCHED):
                if releasing and not cur_val:
                    return None
                if cur_val or prior.state is _ReleaseState.PREPARING:
                    raise _ComPtrBusyError("A reference release is already in progress")
            if cur_val:
                attempt.pointer, attempt.address = self._ptr, cur_val
                attempt.generation = self._gen
                self._release_attempt = attempt
            self._ptr = replacement
            if iface_type is not None:
                self._iface_type = iface_type
            self._bump_gen_locked()
        return attempt if cur_val else None

    def _finish_release(self, item: _ReleaseAttempt, dispatched: bool,
                        completed: bool, error: BaseException | None, refcount: int) -> None:
        """Retain uncertainty; only a proven pre-dispatch failure may restore a slot."""
        with self._lock:
            item.error = None if completed else error
            if completed:
                item.refcount = refcount
                item.state = _ReleaseState.RELEASED
                item.pointer = None
            elif dispatched:
                item.state = _ReleaseState.UNCERTAIN
            elif self._ptr is None:
                self._ptr = item.pointer
                item.pointer = None
                item.state = _ReleaseState.NOT_DISPATCHED
                self._bump_gen_locked()
            else:
                item.state = _ReleaseState.HELD

    def _run_release(self, item: _ReleaseAttempt) -> int:
        """Resolve the vtable before dispatch; finalize even on process-control errors."""
        dispatched = completed = False
        failure: BaseException | None = None
        result = 0
        try:
            iunk = ctypes.cast(item.pointer, ctypes.POINTER(IUnknown))
            if not iunk or not iunk.contents.lpVtbl:
                raise ValueError("Missing IUnknown vtable for owned reference")
            callback = iunk.contents.lpVtbl.contents.Release
            if not callback:
                raise ValueError("Missing IUnknown Release callback")
            this = ctypes.cast(iunk, ctypes.c_void_p)
            with self._lock:
                item.state = _ReleaseState.DISPATCHED
            dispatched = True
            raw_result = callback(this)
            result = int(raw_result) if raw_result is not None else 0
            completed = True
        except COM_PTR_EXCEPTIONS as exc:
            failure = exc
        finally:
            self._finish_release(item, dispatched, completed,
                                 failure if failure is not None else _sys.exception(), result)
        if failure is not None:
            logger.debug("Exception in ComPtr.release(): %s", failure,
                         exc_info=(type(failure), failure, failure.__traceback__))
        else:
            logger.debug("ComPtr.release() rc=%s ptr=0x%016X", result, item.address)
        return result

    def detach_unreleased(self) -> Optional[ctypes.c_void_p]:
        """Transfer ONLY a held reference whose foreign Release was never dispatched.

        The current slot is untouched. Uncertain/in-flight references are rejected;
        this is not permission to retry an ambiguous native operation.
        """
        with self._lock:
            item = self._release_attempt
            if item is None or item.state is not _ReleaseState.HELD:
                raise _ComPtrBusyError("No proven undispatched held reference")
            pointer = item.pointer
            item.pointer = None
            item.state = _ReleaseState.TRANSFERRED
        return pointer

    def __del__(self) -> None:
        """Do not turn garbage collection into an implicit retry of a failed release."""
        try:
            with self._lock:
                prior = self._release_attempt
                failed = prior is not None and prior.state in (
                    _ReleaseState.NOT_DISPATCHED, _ReleaseState.HELD, _ReleaseState.UNCERTAIN)
            if failed:
                logger.debug("ComPtr.__del__ retained failed-release disposition; no retry")
                return
            self.release()
        except COM_PTR_EXCEPTIONS as error:
            logger.debug("ComPtr.__del__ release failed: %s", error, exc_info=True)
