# -*- coding: utf-8 -*-
"""dxgi_structs.py - Struct DXGI critiche per swapchain/present (ctypes).

Obiettivo
---------
Fornire layout ABI affidabili per le struct DXGI più importanti utilizzate
nella pipeline Video (swapchain for composition / present1 / windowless).

Target
------
- Windows SDK 10.0.26100.0 (Win11)
- x64

Nota
----
Questo modulo non sostituisce una validazione `sizeof/offsetof` (vedi tools/abi_harness),
ma è progettato per essere allineato agli header DXGI 1.2+ (dxgi1_2.h).
"""

from __future__ import annotations

import ctypes

from .win_types import BOOL, UINT, RECT, POINT

# DXGI enum sono UINT32
DXGI_FORMAT = UINT
DXGI_SCALING = UINT
DXGI_SWAP_EFFECT = UINT
DXGI_ALPHA_MODE = UINT

# ---------------------------------------------------------------------------
# Struct base
# ---------------------------------------------------------------------------

class DXGI_RATIONAL(ctypes.Structure):
    _fields_ = [
        ("Numerator", UINT),
        ("Denominator", UINT),
    ]

class DXGI_SAMPLE_DESC(ctypes.Structure):
    _fields_ = [
        ("Count", UINT),
        ("Quality", UINT),
    ]

# ---------------------------------------------------------------------------
# Struct per swapchain for composition (DXGI 1.2)
# ---------------------------------------------------------------------------

class DXGI_SWAP_CHAIN_DESC1(ctypes.Structure):
    _fields_ = [
        ("Width", UINT),
        ("Height", UINT),
        ("Format", DXGI_FORMAT),
        ("Stereo", BOOL),
        ("SampleDesc", DXGI_SAMPLE_DESC),
        ("BufferUsage", UINT),
        ("BufferCount", UINT),
        ("Scaling", DXGI_SCALING),
        ("SwapEffect", DXGI_SWAP_EFFECT),
        ("AlphaMode", DXGI_ALPHA_MODE),
        ("Flags", UINT),
    ]

class DXGI_PRESENT_PARAMETERS(ctypes.Structure):
    _fields_ = [
        ("DirtyRectsCount", UINT),
        ("pDirtyRects", ctypes.POINTER(RECT)),
        ("pScrollRect", ctypes.POINTER(RECT)),
        ("pScrollOffset", ctypes.POINTER(POINT)),
    ]

# ---------------------------------------------------------------------------
# Costanti minime utili per smoke test (valori stabili DXGI)
# ---------------------------------------------------------------------------

# DXGI_FORMAT_B8G8R8A8_UNORM (DXGI_FORMAT enum) -> 87
DXGI_FORMAT_B8G8R8A8_UNORM = 87

# DXGI_USAGE_RENDER_TARGET_OUTPUT
DXGI_USAGE_RENDER_TARGET_OUTPUT = 0x00000020

# DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL
DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL = 4

# DXGI_SCALING_STRETCH
DXGI_SCALING_STRETCH = 0

# DXGI_ALPHA_MODE_PREMULTIPLIED
DXGI_ALPHA_MODE_PREMULTIPLIED = 1
