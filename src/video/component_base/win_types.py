# -*- coding: utf-8 -*-
"""win_types.py - Tipi Win32 e helper ABI-safe per ctypes (x64).

Scopo
-----
Questo modulo definisce alias e struct Win32 comunemente usati da DXGI/D3D11/MF/DComp
in modo coerente con il Windows SDK (target: x64).

Nota
----
- Evita dipendenze da comtypes.
- Usa ctypes.wintypes dove disponibile; aggiunge tipi pointer-sized (DWORD_PTR, ULONG_PTR, ecc.).
- ctypes.wintypes NON espone in modo uniforme tutti i typedef (es. HCURSOR) su tutte le build Python:
  qui forniamo fallback robusti.

Target
------
Windows 11, x64.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


def _wt(name: str, default):
    """Ritorna wintypes.<name> se disponibile, altrimenti `default`."""
    return getattr(wintypes, name, default)


# -----------------------------------------------------------------------------
# Primitive/scalari (Win32)
# -----------------------------------------------------------------------------
BOOL = _wt("BOOL", ctypes.c_int)
BYTE = _wt("BYTE", ctypes.c_ubyte)
WORD = _wt("WORD", ctypes.c_ushort)
DWORD = _wt("DWORD", ctypes.c_uint32)
UINT = _wt("UINT", ctypes.c_uint32)
ULONG = _wt("ULONG", ctypes.c_uint32)
LONG = _wt("LONG", ctypes.c_int32)
INT = _wt("INT", ctypes.c_int32)

FLOAT = ctypes.c_float
DOUBLE = ctypes.c_double

# HRESULT è un LONG signed (32-bit). Su Windows, c_long è 32-bit anche su x64.
HRESULT = _wt("HRESULT", ctypes.c_long)

SIZE_T = ctypes.c_size_t


# -----------------------------------------------------------------------------
# Pointer-sized typedef (critici per x64)
# -----------------------------------------------------------------------------
if ctypes.sizeof(ctypes.c_void_p) == 8:
    LONG_PTR = ctypes.c_int64
    ULONG_PTR = ctypes.c_uint64
else:
    LONG_PTR = ctypes.c_long
    ULONG_PTR = ctypes.c_ulong

UINT_PTR = ULONG_PTR
DWORD_PTR = ULONG_PTR
WPARAM = UINT_PTR
LPARAM = LONG_PTR
LRESULT = LONG_PTR


# -----------------------------------------------------------------------------
# Handle types (robusti: fallback a void*)
# -----------------------------------------------------------------------------
HANDLE = _wt("HANDLE", ctypes.c_void_p)
HWND = _wt("HWND", HANDLE)
HINSTANCE = _wt("HINSTANCE", HANDLE)
HMODULE = _wt("HMODULE", HANDLE)
HICON = _wt("HICON", HANDLE)
HCURSOR = _wt("HCURSOR", HANDLE)
HBRUSH = _wt("HBRUSH", HANDLE)
HMENU = _wt("HMENU", HANDLE)
ATOM = _wt("ATOM", WORD)


# -----------------------------------------------------------------------------
# String / pointer typedef
# -----------------------------------------------------------------------------
LPCWSTR = _wt("LPCWSTR", ctypes.c_wchar_p)
LPWSTR = _wt("LPWSTR", ctypes.c_wchar_p)

PVOID = ctypes.c_void_p
LPCVOID = ctypes.c_void_p


# -----------------------------------------------------------------------------
# Struct Win32 comuni
# -----------------------------------------------------------------------------
class POINT(ctypes.Structure):
    _fields_ = [
        ("x", LONG),
        ("y", LONG),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", LONG),
        ("top", LONG),
        ("right", LONG),
        ("bottom", LONG),
    ]


class LUID(ctypes.Structure):
    _fields_ = [
        ("LowPart", DWORD),
        ("HighPart", LONG),
    ]
