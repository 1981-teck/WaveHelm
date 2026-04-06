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


class ComPtr:
    """RAII leggero per un puntatore COM.

    Nota: questa classe NON fa QueryInterface automaticamente; serve solo per:
    - conservare/rilasciare un puntatore
    - castarlo in una POINTER(interface_type) quando richiesto
    """

    __slots__ = ("_ptr", "_iface_type", "_lock", "_gen")

    def __init__(self, ptr: Any = None, iface_type: Optional[Type[Any]] = None) -> None:
        self._ptr: Optional[ctypes.c_void_p] = None
        self._iface_type: Optional[Type[Any]] = iface_type
        self._lock = threading.RLock()
        self._gen: int = 0

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
        """Attacca un puntatore (senza AddRef). Rilascia l'eventuale puntatore precedente.

        Fix bug logico:
        - se ptr rappresenta lo stesso address del puntatore già detenuto, NON chiama release()
          (evita attach(self._ptr) che rilascia l'oggetto e poi lo ri-usa).
        """
        new_val = _coerce_ptr_value(ptr)

        with self._lock:
            cur_val = self._get_ptr_value_locked()
            if new_val and cur_val and new_val == cur_val:
                if iface_type is not None:
                    self._iface_type = iface_type
                return

        # rilascia fuori dal confronto (ma ancora thread-safe perché release prende lock)
        self.release()

        with self._lock:
            if iface_type is not None:
                self._iface_type = iface_type

            if not new_val:
                self._ptr = None
                self._bump_gen_locked()
                return

            self._ptr = ctypes.c_void_p(int(new_val))
            self._bump_gen_locked()


    def adopt(self, ptr: Any, iface_type: Optional[Type[Any]] = None) -> None:
        """Adotta un puntatore come nuova ownership, rilasciando SEMPRE l'esistente.

        Differenza rispetto a attach():
        - attach() è "safe" contro l'edge case `attach(self.ptr)` e quindi, se l'address
          è identico, non rilascia l'oggetto (evita UAF).
        - adopt() è pensato per il caso in cui il chiamante trasferisce un *nuovo* riferimento
          (es. risultato di QueryInterface/AddRef) anche se l'address coincide per motivi
          di aliasing: in tal caso è corretto rilasciare comunque il riferimento precedente.

        Nota:
        - se il chiamante passa solo un cast del puntatore esistente (senza AddRef),
          adopt() può invalidare l'oggetto (UAF). Usare solo quando si è CERTI di trasferire
          una nuova ownership/ref.
        """
        # rilascia sempre il puntatore precedente
        self.release()

        new_val = _coerce_ptr_value(ptr)
        with self._lock:
            if iface_type is not None:
                self._iface_type = iface_type

            if not new_val:
                self._ptr = None
                self._bump_gen_locked()
                return

            self._ptr = ctypes.c_void_p(int(new_val))
            self._bump_gen_locked()

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
        """Rilascia il puntatore COM (IUnknown::Release).

        Miglioramenti:
        - thread-safe: snapshot atomico del puntatore
        - ritorna il refcount risultante (quando disponibile)
        - non invalida self._ptr se Release fallisce con eccezione Python (evita perdere il riferimento
          in condizioni anomale; caller può decidere cosa fare)
        """
        with self._lock:
            val = self._get_ptr_value_locked()
            gen = self._gen
            if not val:
                self._ptr = None
                self._bump_gen_locked()
                return 0

        released_rc = 0
        release_ok = False

        try:
            iunk = ctypes.cast(ctypes.c_void_p(val), ctypes.POINTER(IUnknown))
            if iunk and iunk.contents and iunk.contents.lpVtbl:
                released_rc = iunk.contents.lpVtbl.contents.Release(ctypes.cast(iunk, ctypes.c_void_p))
                released_rc = int(released_rc) if released_rc is not None else 0
                release_ok = True
                logger.debug("ComPtr.release() rc=%s ptr=0x%016X", released_rc, val)
        except COM_PTR_EXCEPTIONS as exc:
            # Non deve mai propagare eccezioni dal finalizer; lascia il puntatore intatto.
            logger.debug("Exception in ComPtr.release(): %s", exc, exc_info=True)
        finally:
            if release_ok:
                # Invalida solo se Release è stato chiamato con successo.
                with self._lock:
                    # se nel frattempo è stato rimpiazzato con un altro ptr, non toccarlo
                    cur_val = self._get_ptr_value_locked()
                    cur_gen = self._gen
                    # Invalida solo se lo stato non è cambiato da quando abbiamo fatto lo snapshot.
                    # Evita invalidazione erronea in caso di re-attach concorrente sullo stesso address
                    # (es. riuso memoria) o in caso di operazioni concorrenti.
                    if cur_val == val and cur_gen == gen:
                        self._ptr = None
                        self._bump_gen_locked()

        return released_rc

    def __del__(self) -> None:
        # Best effort: mai sollevare
        try:
            self.release()
        except COM_PTR_EXCEPTIONS as error:
            logger.debug("ComPtr.__del__ release failed: %s", error, exc_info=True)
