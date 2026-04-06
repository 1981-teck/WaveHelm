# -*- coding: utf-8 -*-
"""iid_registry.py - GUID/IID/CLSID canonici (SDK 10.0.26100.0).

Questo file è generato a partire dagli header del tuo Windows SDK locale (10.0.26100.0)
inclusi nel kit. Serve per eliminare ambiguità e allineare in modo deterministico
tutte le QueryInterface/CoCreateInstance/attributi MF.

Nota: i valori sono normalizzati in lowercase per confronto stringhe; la classe GUID
preserva il layout binario corretto.
"""

from __future__ import annotations

from .definitions import GUID

# ---------------------------------------------------------------------------
# DXGI
# ---------------------------------------------------------------------------

IID_IDXGIDevice = GUID.from_string("{54ec77fa-1377-44e6-8c32-88fd5f44c84c}")
IID_IDXGIFactory2 = GUID.from_string("{50c83a1c-e072-4c48-87b0-3630fa36a6d0}")

# ---------------------------------------------------------------------------
# DirectComposition
# ---------------------------------------------------------------------------

IID_IDCompositionDevice = GUID.from_string("{c37ea93a-e7aa-450d-b16f-9746cb0407f3}")
IID_IDCompositionTarget = GUID.from_string("{eacdd04c-117e-4e17-88f4-d1b12b0e3d89}")
IID_IDCompositionVisual = GUID.from_string("{4d93059d-097b-4651-9a60-f0f25116e2f3}")

# ---------------------------------------------------------------------------
# Media Foundation / MediaEngine
# ---------------------------------------------------------------------------

CLSID_MFMediaEngineClassFactory = GUID.from_string("{b44392da-499b-446b-a4cb-005fead0e6d5}")
IID_IMFMediaEngineClassFactory = GUID.from_string("{4d645ace-26aa-4688-9be1-df3516990b93}")
IID_IMFMediaEngineNotify = GUID.from_string("{fee7c112-e776-42b5-9bbf-0048524e2bd5}")
IID_IMFMediaEngine = GUID.from_string("{98a1b0bb-03eb-4935-ae7c-93c1fa0e1c93}")
IID_IMFMediaEngineEx = GUID.from_string("{83015ead-b1e6-40d0-a98a-37145ffe1ad1}")

# MF media engine attributes
MF_MEDIA_ENGINE_PLAYBACK_HWND = GUID.from_string("{d988879b-67c9-4d92-baa7-6eadd446039d}")
MF_MEDIA_ENGINE_DXGI_MANAGER = GUID.from_string("{065702da-1094-486d-8617-ee7cc4ee4648}")
MF_MEDIA_ENGINE_VIDEO_OUTPUT_FORMAT = GUID.from_string("{5066893c-8cf9-42bc-8b8a-472212e52726}")
