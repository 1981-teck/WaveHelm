# -*- coding: utf-8 -*-
"""d3d11_structs.py - Struct D3D11 critiche (ctypes).

Obiettivo
---------
Layout ABI per le struct più usate nel renderer D3D11.

Target
------
- Windows SDK 10.0.26100.0 (Win11)
- x64
"""

from __future__ import annotations

import ctypes

from .win_types import UINT, FLOAT, LPCVOID
from .dxgi_structs import DXGI_SAMPLE_DESC, DXGI_FORMAT

# D3D11 enum/flags sono UINT
D3D11_USAGE = UINT

class D3D11_TEXTURE2D_DESC(ctypes.Structure):
    _fields_ = [
        ("Width", UINT),
        ("Height", UINT),
        ("MipLevels", UINT),
        ("ArraySize", UINT),
        ("Format", DXGI_FORMAT),
        ("SampleDesc", DXGI_SAMPLE_DESC),
        ("Usage", D3D11_USAGE),
        ("BindFlags", UINT),
        ("CPUAccessFlags", UINT),
        ("MiscFlags", UINT),
    ]

class D3D11_SUBRESOURCE_DATA(ctypes.Structure):
    _fields_ = [
        ("pSysMem", LPCVOID),
        ("SysMemPitch", UINT),
        ("SysMemSlicePitch", UINT),
    ]

class D3D11_BOX(ctypes.Structure):
    _fields_ = [
        ("left", UINT),
        ("top", UINT),
        ("front", UINT),
        ("right", UINT),
        ("bottom", UINT),
        ("back", UINT),
    ]

class D3D11_VIEWPORT(ctypes.Structure):
    _fields_ = [
        ("TopLeftX", FLOAT),
        ("TopLeftY", FLOAT),
        ("Width", FLOAT),
        ("Height", FLOAT),
        ("MinDepth", FLOAT),
        ("MaxDepth", FLOAT),
    ]

# D3D11CreateDevice flags (subset per smoke test)
D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20
D3D11_CREATE_DEVICE_DEBUG = 0x2
