# -*- coding: utf-8 -*-
"""Runtime bootstrap bindings for Win32/Media Foundation low-level access.

This module isolates DLL loading policy and startup/runtime helpers used by
Media Foundation wrappers. Keeping these bindings separate reduces the size of
``definitions.py`` without changing its public surface.
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import c_void_p, wintypes
from typing import Any, Callable

logger = logging.getLogger(__name__)

MF_VERSION_PARSE_EXCEPTIONS = (TypeError, ValueError)

# --- Media Foundation startup constants ---
#
# MF_VERSION comes from Media Foundation headers (MFAPI.h). The environment
# variable is retained for tests/regressions that need to force a specific
# version value.
_MF_VERSION_DEFAULT = 0x00020070
try:
    MF_VERSION = int(os.environ.get("WAVEHELM_MF_VERSION", str(_MF_VERSION_DEFAULT)), 0)
except MF_VERSION_PARSE_EXCEPTIONS:
    MF_VERSION = _MF_VERSION_DEFAULT

MFSTARTUP_FULL = 0x0
MFSTARTUP_LITE = 0x1

# DLL loading policy:
# - default: strict fail-fast on Windows production environments
# - tests / partial environments: WAVEHELM_STRICT_DLL_LOAD=0 downgrades missing
#   DLLs to non-fatal proxies so import-time getattr checks can still work.
_STRICT_DLL_LOAD = os.environ.get("WAVEHELM_STRICT_DLL_LOAD", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)


class _MissingDLL:
    """Proxy for a DLL that could not be loaded.

    Edge cases handled deterministically:
    1. Partial test environments where Media Foundation DLLs are absent.
    2. Direct attribute access to missing exports after a degraded import.
    3. Truthiness checks used by callers to decide whether a binding exists.
    """

    __slots__ = ("name", "error")

    def __init__(self, name: str, error: BaseException):
        self.name = name
        self.error = error

    def __getattr__(self, item: str):
        raise AttributeError(f"DLL '{self.name}' non caricata (richiesto simbolo '{item}'): {self.error}")

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"<MissingDLL name={self.name!r} error={self.error!r}>"


class _MissingFunction:
    """Callable placeholder for missing DLL exports.

    It keeps module import stable on unsupported platforms while still failing
    explicitly if the caller tries to execute the missing Win32 binding.
    """

    __slots__ = ("dll_name", "symbol", "error", "argtypes", "restype")

    def __init__(self, dll_name: str, symbol: str, error: BaseException):
        self.dll_name = dll_name
        self.symbol = symbol
        self.error = error
        self.argtypes = None
        self.restype = None

    def __call__(self, *args: Any, **kwargs: Any):
        raise RuntimeError(
            f"Funzione Win32 non disponibile: {self.dll_name}!{self.symbol} ({self.error})"
        ) from self.error

    def __repr__(self) -> str:
        return (
            f"<MissingFunction dll={self.dll_name!r} symbol={self.symbol!r} "
            f"error={self.error!r}>"
        )


def _load_windll(name: str, *, use_last_error: bool = True, required: bool = True) -> ctypes.WinDLL:
    """Load a DLL with deterministic error handling.

    Edge cases handled deterministically:
    1. Missing DLL on a strict Windows runtime -> raises OSError immediately.
    2. Missing optional DLL in tests/partial environments -> returns _MissingDLL.
    3. Non-Windows runtimes without ``ctypes.WinDLL`` -> returns _MissingDLL so
       test collection can proceed and platform guards can decide later.
    """
    windll_loader = getattr(ctypes, "WinDLL", None)
    if windll_loader is None:
        exc = AttributeError("ctypes.WinDLL non disponibile su questa piattaforma")
        logger.warning("WinDLL non disponibile; binding '%s' marcato come assente", name)
        # type: ignore[return-value]  # proxy compatibile per getattr/diagnostica
        return _MissingDLL(name, exc)  # type: ignore[return-value]

    try:
        return windll_loader(name, use_last_error=use_last_error)
    except OSError as exc:
        if required and _STRICT_DLL_LOAD:
            raise OSError(f"Impossibile caricare la DLL '{name}': {exc}") from exc

        logger.warning("DLL '%s' non caricata (required=%s, strict=%s): %s", name, required, _STRICT_DLL_LOAD, exc)
        # type: ignore[return-value]  # proxy compatibile per getattr/diagnostica
        return _MissingDLL(name, exc)  # type: ignore[return-value]


def _bind_function(
    dll: Any,
    dll_name: str,
    symbol: str,
    *,
    argtypes: list[Any],
    restype: Any,
) -> Callable[..., Any]:
    """Resolve an exported function without breaking import-time on degraded runtimes."""
    try:
        func = getattr(dll, symbol)
    except AttributeError as exc:
        func = _MissingFunction(dll_name, symbol, exc)

    func.argtypes = argtypes
    func.restype = restype
    return func


# ole32.dll: API COM (CoInitializeEx, CoWaitForMultipleHandles, ...)
_ole32 = _load_windll("ole32")
# oleaut32.dll: BSTR (SysAllocString, SysFreeString, ...)
_oleaut32 = _load_windll("oleaut32")

_kernel32 = _load_windll("kernel32")
_user32 = _load_windll("user32")
_mfplat = _load_windll("mfplat.dll", use_last_error=True, required=True)

# --- COM Threading ---
COINIT_APARTMENTTHREADED = 0x2
COWAIT_DISPATCH_CALLS = 0x8
COWAIT_DISPATCH_WINDOW_MESSAGES = 0x10
COWAIT_DISPATCH_ALL = COWAIT_DISPATCH_CALLS | COWAIT_DISPATCH_WINDOW_MESSAGES

# --- BSTR helpers ---
_SysAllocString = _bind_function(
    _oleaut32,
    "oleaut32",
    "SysAllocString",
    argtypes=[wintypes.LPCWSTR],
    restype=c_void_p,
)

_SysFreeString = _bind_function(
    _oleaut32,
    "oleaut32",
    "SysFreeString",
    argtypes=[c_void_p],
    restype=None,
)

# --- Win32 helpers ---
_IsWindow = _bind_function(
    _user32,
    "user32",
    "IsWindow",
    argtypes=[wintypes.HWND],
    restype=wintypes.BOOL,
)
