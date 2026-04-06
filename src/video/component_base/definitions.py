# -*- coding: utf-8 -*-
"""definitions.py
Definizioni di basso livello (ctypes, GUID, costanti, funzioni Win32) per
l'interfacciamento con Windows Media Foundation e COM.

Fix principali rispetto alla versione precedente:
- Caricamento DLL con gestione errori esplicita (WinDLL con use_last_error)
- MF_VERSION configurabile via env (fallback al valore standard degli header)
- IMFAttributesVtbl con firme COMPLETE (niente placeholder c_void_p)
- Evitato conflitto di nomi tra interfacce ctypes e comtypes (factory)
- Stub comtypes che falliscono in modo esplicito se usati
"""

from __future__ import annotations

import ctypes
import logging
import os
import uuid
from ctypes import (
    POINTER,
    Structure,
    Union,
    byref,
    cast,
    c_bool,
    c_double,
    c_long,
    c_ubyte,
    c_uint16,
    c_uint32,
    c_uint64,
    c_ulong,
    c_void_p,
    wintypes,
)

from .definitions_comtypes import load_comtypes_symbols
from .definitions_events import (
    MF_MEDIA_ENGINE_EVENT_ABORT,
    MF_MEDIA_ENGINE_EVENT_CANPLAY,
    MF_MEDIA_ENGINE_EVENT_CANPLAYTHROUGH,
    MF_MEDIA_ENGINE_EVENT_DURATIONCHANGE,
    MF_MEDIA_ENGINE_EVENT_EMPTIED,
    MF_MEDIA_ENGINE_EVENT_ENDED,
    MF_MEDIA_ENGINE_EVENT_ERROR,
    MF_MEDIA_ENGINE_EVENT_FORMATCHANGE,
    MF_MEDIA_ENGINE_EVENT_LOADEDDATA,
    MF_MEDIA_ENGINE_EVENT_LOADEDMETADATA,
    MF_MEDIA_ENGINE_EVENT_LOADSTART,
    MF_MEDIA_ENGINE_EVENT_PAUSE,
    MF_MEDIA_ENGINE_EVENT_PLAY,
    MF_MEDIA_ENGINE_EVENT_PLAYING,
    MF_MEDIA_ENGINE_EVENT_PROGRESS,
    MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS,
    MF_MEDIA_ENGINE_EVENT_RATECHANGE,
    MF_MEDIA_ENGINE_EVENT_SEEKED,
    MF_MEDIA_ENGINE_EVENT_SEEKING,
    MF_MEDIA_ENGINE_EVENT_STALLED,
    MF_MEDIA_ENGINE_EVENT_SUSPEND,
    MF_MEDIA_ENGINE_EVENT_TIMEUPDATE,
    MF_MEDIA_ENGINE_EVENT_VOLUMECHANGE,
    MF_MEDIA_ENGINE_EVENT_WAITING,
)
from .definitions_runtime import (
    COINIT_APARTMENTTHREADED,
    COWAIT_DISPATCH_ALL,
    COWAIT_DISPATCH_CALLS,
    COWAIT_DISPATCH_WINDOW_MESSAGES,
    MFSTARTUP_FULL,
    MFSTARTUP_LITE,
    MF_VERSION,
    MF_VERSION_PARSE_EXCEPTIONS,
    _IsWindow,
    _MissingDLL,
    _SysAllocString,
    _SysFreeString,
    _STRICT_DLL_LOAD,
    _kernel32,
    _load_windll,
    _mfplat,
    _ole32,
    _oleaut32,
    _user32,
)

logger = logging.getLogger(__name__)

# _WINFUNCTYPE esiste solo su Windows.
# Per consentire import/collezione test fuori Windows usiamo un alias
# degradato a CFUNCTYPE, sufficiente per costruire le signature ctypes
# senza pretendere supporto reale al calling convention Win32.
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)

# ---------------------------------------------------------------------------
# Tipi di base / HRESULT
# ---------------------------------------------------------------------------

HRESULT = c_long
S_OK = 0

def _hr_ok(hr: int) -> bool:
    """Controlla se un HRESULT indica successo."""
    return hr >= 0

# ---------------------------------------------------------------------------
# GUID (ctypes)
# ---------------------------------------------------------------------------

class GUID(Structure):
    _fields_ = [
        ("Data1", c_uint32),
        ("Data2", c_uint16),
        ("Data3", c_uint16),
        ("Data4", c_ubyte * 8),
    ]

    def __init__(
        self,
        d1: int,
        d2: int,
        d3: int,
        b1: int,
        b2: int,
        b3: int,
        b4: int,
        b5: int,
        b6: int,
        b7: int,
        b8: int,
    ):
        super().__init__()
        self.Data1 = c_uint32(d1)
        self.Data2 = c_uint16(d2)
        self.Data3 = c_uint16(d3)
        self.Data4 = (c_ubyte * 8)(b1, b2, b3, b4, b5, b6, b7, b8)

    @classmethod
    def from_string(cls, guid_str: str) -> "GUID":
        """Crea un GUID ctypes a partire da una stringa {XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}."""
        u = uuid.UUID(guid_str.strip("{}"))
        b = u.bytes_le  # little-endian layout per GUID struct Win32
        d1 = int.from_bytes(b[0:4], "little")
        d2 = int.from_bytes(b[4:6], "little")
        d3 = int.from_bytes(b[6:8], "little")
        d4 = list(b[8:16])
        return cls(d1, d2, d3, *d4)

    def __repr__(self) -> str:
        d4 = bytes(self.Data4)
        return (
            f"{{{self.Data1:08X}-{self.Data2:04X}-{self.Data3:04X}-"
            f"{d4[0]:02X}{d4[1]:02X}-{d4[2]:02X}{d4[3]:02X}{d4[4]:02X}{d4[5]:02X}{d4[6]:02X}{d4[7]:02X}}}"
        )

# ---------------------------------------------------------------------------
# COM base
# ---------------------------------------------------------------------------

class IUnknownVtbl(Structure):
    _fields_ = [
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
    ]

class IUnknown(Structure):
    _fields_ = [("lpVtbl", POINTER(IUnknownVtbl))]

class IClassFactoryVtbl(Structure):
    _fields_ = [
        # IUnknown methods
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
        # IClassFactory methods
        ("CreateInstance", _WINFUNCTYPE(HRESULT, c_void_p, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("LockServer", _WINFUNCTYPE(HRESULT, c_void_p, wintypes.BOOL)),
    ]

class IClassFactory(Structure):
    _fields_ = [("lpVtbl", POINTER(IClassFactoryVtbl))]

# ---------------------------------------------------------------------------
# Media Foundation GUIDs
# ---------------------------------------------------------------------------

IID_IClassFactory = GUID(0x00000001, 0x0000, 0x0000, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46)

# Source of truth (string) per evitare confusione tra formati
IID_IMFMediaEngineNotify_STR = "{FEE7C112-E776-42B5-9BBF-0048524E2BD5}"
IID_IMFMediaEngineNotify = GUID.from_string(IID_IMFMediaEngineNotify_STR)

MF_MEDIA_ENGINE_PLAYBACK_HWND = GUID(0xD988879B, 0x67C9, 0x4D92, 0xBA, 0xA7, 0x6E, 0xAD, 0xD4, 0x46, 0x03, 0x9D)
MF_MEDIA_ENGINE_CALLBACK = GUID(0xC60381B8, 0x83A4, 0x41F8, 0xA3, 0xD0, 0xDE, 0x05, 0x07, 0x68, 0x49, 0xA9)
MF_MEDIA_ENGINE_VIDEO_OUTPUT_FORMAT = GUID(0x5066893C, 0x8CF9, 0x42BC, 0x8B, 0x8A, 0x47, 0x22, 0x12, 0xE5, 0x27, 0x26)

# Presente nell'originale: usato in MediaEngineCore
MF_MEDIA_ENGINE_AUDIO_CATEGORY = GUID(0x4D8FAD4E, 0x0CFB, 0x434B, 0xA8, 0xEE, 0x80, 0xD0, 0x10, 0xA1, 0x5B, 0x5D)

# Presente nell'originale: usato in MediaEngineCore
DXGI_FORMAT_B8G8R8A8_UNORM = 87

# ---------------------------------------------------------------------------
# MF base types per IMFAttributes
# ---------------------------------------------------------------------------

# MF_ATTRIBUTE_TYPE (enum)
MF_ATTRIBUTE_TYPE = c_uint32

# MF_ATTRIBUTES_MATCH_TYPE (enum)
MF_ATTRIBUTES_MATCH_TYPE = c_uint32

# Dimensione del payload union: 16 bytes su 64-bit, 8 bytes su 32-bit.
_PROPVARIANT_RAW = (ctypes.c_ulonglong * 2) if ctypes.sizeof(c_void_p) == 8 else (ctypes.c_ulong * 2)

class _PROPVARIANT_UNION(Union):
    _fields_ = [
        ("llVal", ctypes.c_longlong),
        ("ullVal", ctypes.c_ulonglong),
        ("dblVal", c_double),
        ("pwszVal", wintypes.LPWSTR),
        ("punkVal", c_void_p),
        ("p", c_void_p),
        ("_raw", _PROPVARIANT_RAW),  # raw storage (size depends on arch)
    ]

class PROPVARIANT(Structure):
    """PROPVARIANT (layout compatibile).

    Obiettivo principale: ABI/layout corretto per IMFAttributes (GetItem/SetItem/...).

    Note importanti:
    - Questa definizione NON copre in modo esaustivo tutti i campi semantici possibili
      (es. DECIMAL/DATE/cyVal/SAFEARRAY/etc.). Il payload viene trattato come union/raw.
    - Se devi interpretare il contenuto in base a `vt`, estendi `_PROPVARIANT_UNION`
      aggiungendo i campi necessari oppure usa API dedicate (PropVariantClear, ecc.).
    """
    _fields_ = [
        ("vt", c_uint16),
        ("wReserved1", c_uint16),
        ("wReserved2", c_uint16),
        ("wReserved3", c_uint16),
        ("value", _PROPVARIANT_UNION),
    ]

# ---------------------------------------------------------------------------
# MF Interfaces (forward declarations)
# ---------------------------------------------------------------------------

class IMFAttributes(Structure):
    pass

class IMFMediaEngine(IUnknown):
    pass

class IMFMediaError(IUnknown):
    pass

class IMFDXGIDeviceManager(IUnknown):
    pass

class IMFTimedText(Structure):
    pass

class IMFTimedTextTrackList(Structure):
    pass

class IMFTimedTextTrack(Structure):
    pass

class IMFTimedTextNotify(Structure):
    pass

# MF timed-text enums are represented as UINT32 in the ABI.
MF_TIMED_TEXT_TRACK_KIND = c_uint32
MF_TIMED_TEXT_ERROR_CODE = c_uint32

# ---------------------------------------------------------------------------
# IMFAttributes vtable (COM) - FIRME COMPLETE
# ---------------------------------------------------------------------------

class IMFAttributesVtbl(Structure):
    _fields_ = [
        # IUnknown
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),

        # IMFAttributes
        ("GetItem", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(PROPVARIANT))),
        ("GetItemType", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(MF_ATTRIBUTE_TYPE))),
        ("CompareItem", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(PROPVARIANT), POINTER(wintypes.BOOL))),
        ("Compare", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(IMFAttributes), MF_ATTRIBUTES_MATCH_TYPE, POINTER(wintypes.BOOL))),
        ("GetUINT32", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_uint32))),
        ("GetUINT64", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_uint64))),
        ("GetDouble", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_double))),
        ("GetGUID", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(GUID))),
        ("GetStringLength", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_uint32))),
        ("GetString", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), wintypes.LPWSTR, c_uint32, POINTER(c_uint32))),
        ("GetAllocatedString", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(wintypes.LPWSTR), POINTER(c_uint32))),
        ("GetBlobSize", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_uint32))),
        ("GetBlob", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(ctypes.c_ubyte), c_uint32, POINTER(c_uint32))),
        ("GetAllocatedBlob", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(POINTER(ctypes.c_ubyte)), POINTER(c_uint32))),
        ("GetUnknown", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(GUID), POINTER(c_void_p))),
        ("SetItem", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(PROPVARIANT))),
        ("DeleteItem", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID))),
        ("DeleteAllItems", _WINFUNCTYPE(HRESULT, c_void_p)),

        ("SetUINT32", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), c_uint32)),
        ("SetUINT64", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), c_uint64)),
        ("SetDouble", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), c_double)),
        ("SetGUID", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(GUID))),
        ("SetString", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), wintypes.LPCWSTR)),
        ("SetBlob", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(ctypes.c_ubyte), c_uint32)),
        # SetUnknown: accetta qualsiasi puntatore a IUnknown (c_void_p è compatibile)
        ("SetUnknown", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), c_void_p)),

        ("LockStore", _WINFUNCTYPE(HRESULT, c_void_p)),
        ("UnlockStore", _WINFUNCTYPE(HRESULT, c_void_p)),
        ("GetCount", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_uint32))),
        ("GetItemByIndex", _WINFUNCTYPE(HRESULT, c_void_p, c_uint32, POINTER(GUID), POINTER(PROPVARIANT))),
        ("CopyAllItems", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(IMFAttributes))),
    ]

IMFAttributes._fields_ = [("lpVtbl", POINTER(IMFAttributesVtbl))]

# ---------------------------------------------------------------------------
# IMFDXGIDeviceManager VTable (come in originale)
# ---------------------------------------------------------------------------

class IMFDXGIDeviceManagerVtbl(Structure):
    _fields_ = [
        # Metodi base IUnknown
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
        # Metodi specifici IMFDXGIDeviceManager
        ("CloseDeviceHandle", _WINFUNCTYPE(HRESULT, c_void_p, c_void_p)),
        ("GetVideoService", _WINFUNCTYPE(HRESULT, c_void_p, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("LockDevice", _WINFUNCTYPE(HRESULT, c_void_p, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("OpenDeviceHandle", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_void_p))),
        ("ResetDevice", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(IUnknown), c_uint32)),
        ("TestDevice", _WINFUNCTYPE(HRESULT, c_void_p, c_void_p)),
        ("UnlockDevice", _WINFUNCTYPE(HRESULT, c_void_p, c_void_p, wintypes.BOOL)),
    ]


# ---------------------------------------------------------------------------
# IMFTimedText / subtitle-related COM interfaces
# ---------------------------------------------------------------------------

class IMFTimedTextNotifyVtbl(Structure):
    _fields_ = [
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
    ]


class IMFTimedTextTrackVtbl(Structure):
    _fields_ = [
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("GetDataFormat", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID))),
        ("GetErrorCode", _WINFUNCTYPE(MF_TIMED_TEXT_ERROR_CODE, c_void_p)),
        ("GetExtendedErrorCode", _WINFUNCTYPE(HRESULT, c_void_p)),
        ("GetId", _WINFUNCTYPE(wintypes.DWORD, c_void_p)),
        ("GetInBandMetadataTrackDispatchType", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(wintypes.LPWSTR))),
        ("GetLabel", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(wintypes.LPWSTR))),
        ("GetLanguage", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(wintypes.LPWSTR))),
        ("GetTrackKind", _WINFUNCTYPE(MF_TIMED_TEXT_TRACK_KIND, c_void_p)),
        ("IsActive", _WINFUNCTYPE(wintypes.BOOL, c_void_p)),
        ("IsInBand", _WINFUNCTYPE(wintypes.BOOL, c_void_p)),
        ("SetLabel", _WINFUNCTYPE(HRESULT, c_void_p, wintypes.LPCWSTR)),
    ]


class IMFTimedTextTrackListVtbl(Structure):
    _fields_ = [
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("GetLength", _WINFUNCTYPE(wintypes.DWORD, c_void_p)),
        ("GetTrack", _WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD, POINTER(POINTER(IMFTimedTextTrack)))),
        ("GetTrackById", _WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD, POINTER(POINTER(IMFTimedTextTrack)))),
    ]


class IMFTimedTextVtbl(Structure):
    _fields_ = [
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("AddDataSource", _WINFUNCTYPE(HRESULT, c_void_p, c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR, MF_TIMED_TEXT_TRACK_KIND, wintypes.BOOL, POINTER(wintypes.DWORD))),
        ("AddDataSourceFromUrl", _WINFUNCTYPE(HRESULT, c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, MF_TIMED_TEXT_TRACK_KIND, wintypes.BOOL, POINTER(wintypes.DWORD))),
        ("GetActiveTracks", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(POINTER(IMFTimedTextTrackList)))),
        ("GetCueTimeOffset", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_double))),
        ("GetMetadataTracks", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(POINTER(IMFTimedTextTrackList)))),
        ("GetTextTracks", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(POINTER(IMFTimedTextTrackList)))),
        ("GetTracks", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(POINTER(IMFTimedTextTrackList)))),
        ("IsInBandEnabled", _WINFUNCTYPE(wintypes.BOOL, c_void_p)),
        ("RegisterNotifications", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(IMFTimedTextNotify))),
        ("RemoveTrack", _WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD)),
        ("SelectTrack", _WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD, wintypes.BOOL)),
        ("SetCueTimeOffset", _WINFUNCTYPE(HRESULT, c_void_p, c_double)),
        ("SetInBandEnabled", _WINFUNCTYPE(HRESULT, c_void_p, wintypes.BOOL)),
    ]


IMFTimedTextNotify._fields_ = [("lpVtbl", POINTER(IMFTimedTextNotifyVtbl))]
IMFTimedTextTrack._fields_ = [("lpVtbl", POINTER(IMFTimedTextTrackVtbl))]
IMFTimedTextTrackList._fields_ = [("lpVtbl", POINTER(IMFTimedTextTrackListVtbl))]
IMFTimedText._fields_ = [("lpVtbl", POINTER(IMFTimedTextVtbl))]

# ---------------------------------------------------------------------------
# IMFMediaEngineClassFactory (ctypes) - EVITA conflitto con comtypes
# ---------------------------------------------------------------------------

class IMFMediaEngineClassFactoryVtbl(Structure):
    _fields_ = [
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
        # HRESULT CreateInstance(DWORD dwFlags, IMFAttributes *pAttr, IMFMediaEngine **ppEngine)
        ("CreateInstance", _WINFUNCTYPE(HRESULT, c_void_p, c_uint32, POINTER(IMFAttributes), POINTER(POINTER(IMFMediaEngine)))),
    ]

class IMFMediaEngineClassFactory(Structure):
    _fields_ = [("lpVtbl", POINTER(IMFMediaEngineClassFactoryVtbl))]

# ---------------------------------------------------------------------------
# IMFMediaEngine VTable specs (index-based calls)
# ---------------------------------------------------------------------------

_IMF_MEDIA_ENGINE_VTBL_SPECS = {
    # Indici derivati da mfmediaengine.h (IUnknown + IMFMediaEngine).
    "SetSource": {"index": 6, "restype": HRESULT, "argtypes": [c_void_p]},  # BSTR
    "Load": {"index": 12, "restype": HRESULT, "argtypes": []},
    "GetCurrentTime": {"index": 16, "restype": c_double, "argtypes": []},
    "SetCurrentTime": {"index": 17, "restype": HRESULT, "argtypes": [c_double]},
    "GetDuration": {"index": 19, "restype": c_double, "argtypes": []},
    "GetEnded": {"index": 27, "restype": wintypes.BOOL, "argtypes": []},
    "IsEnded": {"index": 27, "restype": wintypes.BOOL, "argtypes": []},
    "GetLoop": {"index": 30, "restype": wintypes.BOOL, "argtypes": []},
    "SetLoop": {"index": 31, "restype": HRESULT, "argtypes": [wintypes.BOOL]},
    "Play": {"index": 32, "restype": HRESULT, "argtypes": []},
    "Pause": {"index": 33, "restype": HRESULT, "argtypes": []},
    "GetMuted": {"index": 34, "restype": wintypes.BOOL, "argtypes": []},
    "SetMuted": {"index": 35, "restype": HRESULT, "argtypes": [wintypes.BOOL]},
    "GetVolume": {"index": 36, "restype": c_double, "argtypes": []},
    "SetVolume": {"index": 37, "restype": HRESULT, "argtypes": [c_double]},
    "HasVideo": {"index": 38, "restype": wintypes.BOOL, "argtypes": []},
    "HasAudio": {"index": 39, "restype": wintypes.BOOL, "argtypes": []},
    "GetNativeVideoSize": {"index": 40, "restype": HRESULT, "argtypes": [POINTER(wintypes.DWORD), POINTER(wintypes.DWORD)]},
    "GetVideoAspectRatio": {"index": 41, "restype": HRESULT, "argtypes": [POINTER(wintypes.DWORD), POINTER(wintypes.DWORD)]},
    "Shutdown": {"index": 42, "restype": HRESULT, "argtypes": []},
    "TransferVideoFrame": {"index": 43, "restype": HRESULT, "argtypes": [c_void_p, c_void_p, c_void_p, c_void_p]},
    "OnVideoStreamTick": {"index": 44, "restype": HRESULT, "argtypes": [c_void_p]},
    # IMFMediaEngineEx methods. Source of truth for order/signatures:
    # mfmediaengine.h / IMFMediaEngineEx declaration order.
    "SetSourceFromByteStream": {"index": 45, "restype": HRESULT, "argtypes": [c_void_p, c_void_p]},
    "GetStatistics": {"index": 46, "restype": HRESULT, "argtypes": [c_uint32, POINTER(PROPVARIANT)]},
    "UpdateVideoStream": {"index": 47, "restype": HRESULT, "argtypes": [c_void_p, POINTER(wintypes.RECT), c_void_p]},
    "GetBalance": {"index": 48, "restype": c_double, "argtypes": []},
    "SetBalance": {"index": 49, "restype": HRESULT, "argtypes": [c_double]},
    "IsPlaybackRateSupported": {"index": 50, "restype": wintypes.BOOL, "argtypes": [c_double]},
    "FrameStep": {"index": 51, "restype": HRESULT, "argtypes": [wintypes.BOOL]},
    "GetResourceCharacteristics": {"index": 52, "restype": HRESULT, "argtypes": [POINTER(wintypes.DWORD)]},
    "GetPresentationAttribute": {"index": 53, "restype": HRESULT, "argtypes": [POINTER(GUID), POINTER(PROPVARIANT)]},
    "GetNumberOfStreams": {"index": 54, "restype": HRESULT, "argtypes": [POINTER(wintypes.DWORD)]},
    "GetStreamAttribute": {
        "index": 55,
        "restype": HRESULT,
        "argtypes": [wintypes.DWORD, POINTER(GUID), POINTER(PROPVARIANT)],
    },
    "GetStreamSelection": {"index": 56, "restype": HRESULT, "argtypes": [wintypes.DWORD, POINTER(wintypes.BOOL)]},
    "SetStreamSelection": {"index": 57, "restype": HRESULT, "argtypes": [wintypes.DWORD, wintypes.BOOL]},
    "ApplyStreamSelections": {"index": 58, "restype": HRESULT, "argtypes": []},
    "IsProtected": {"index": 59, "restype": HRESULT, "argtypes": [POINTER(wintypes.BOOL)]},
    "InsertVideoEffect": {"index": 60, "restype": HRESULT, "argtypes": [c_void_p, wintypes.BOOL]},
    "InsertAudioEffect": {"index": 61, "restype": HRESULT, "argtypes": [c_void_p, wintypes.BOOL]},
    "RemoveAllEffects": {"index": 62, "restype": HRESULT, "argtypes": []},
    "SetTimelineMarkerTimer": {"index": 63, "restype": HRESULT, "argtypes": [c_double]},
    "GetTimelineMarkerTimer": {"index": 64, "restype": HRESULT, "argtypes": [POINTER(c_double)]},
    "CancelTimelineMarkerTimer": {"index": 65, "restype": HRESULT, "argtypes": []},
    "IsStereo3D": {"index": 66, "restype": wintypes.BOOL, "argtypes": []},
    "GetStereo3DFramePackingMode": {"index": 67, "restype": HRESULT, "argtypes": [POINTER(c_uint32)]},
    "SetStereo3DFramePackingMode": {"index": 68, "restype": HRESULT, "argtypes": [c_uint32]},
    "GetStereo3DRenderMode": {"index": 69, "restype": HRESULT, "argtypes": [POINTER(c_uint32)]},
    "SetStereo3DRenderMode": {"index": 70, "restype": HRESULT, "argtypes": [c_uint32]},
    "EnableWindowlessSwapchainMode": {"index": 71, "restype": HRESULT, "argtypes": [wintypes.BOOL]},
    "GetVideoSwapchainHandle": {"index": 72, "restype": HRESULT, "argtypes": [POINTER(c_void_p)]},
    "EnableHorizontalMirrorMode": {"index": 73, "restype": HRESULT, "argtypes": [wintypes.BOOL]},
    "GetAudioStreamCategory": {"index": 74, "restype": HRESULT, "argtypes": [POINTER(c_uint32)]},
    "SetAudioStreamCategory": {"index": 75, "restype": HRESULT, "argtypes": [c_uint32]},
    "GetAudioEndpointRole": {"index": 76, "restype": HRESULT, "argtypes": [POINTER(c_uint32)]},
    "SetAudioEndpointRole": {"index": 77, "restype": HRESULT, "argtypes": [c_uint32]},
    "GetRealTimeMode": {"index": 78, "restype": HRESULT, "argtypes": [POINTER(wintypes.BOOL)]},
    "SetRealTimeMode": {"index": 79, "restype": HRESULT, "argtypes": [wintypes.BOOL]},
    "SetCurrentTimeEx": {"index": 80, "restype": HRESULT, "argtypes": [c_double, c_uint32]},
    "EnableTimeUpdateTimer": {"index": 81, "restype": HRESULT, "argtypes": [wintypes.BOOL]},
}

(
    comtypes,
    _COMTYPES_AVAILABLE,
    COMObject,
    CT_IUnknown,
    CT_GUID,
    COMMETHOD,
    CT_HRESULT,
    IMFMediaEngineNotify,
    CT_IMFMediaEngineClassFactory,
) = load_comtypes_symbols(IID_IMFMediaEngineNotify_STR)
