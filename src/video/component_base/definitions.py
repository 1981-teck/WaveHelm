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
    _SysAllocString,
    _SysFreeString,
    _mfplat,
    _ole32,
)

logger = logging.getLogger(__name__)

from .definitions_abi import (
    GUID,
    HRESULT,
    IClassFactory,
    IClassFactoryVtbl,
    IMFAttributes,
    IMFAttributesVtbl,
    IMFDXGIDeviceManager,
    IMFDXGIDeviceManagerVtbl,
    IMFMediaEngine,
    IMFMediaEngineClassFactory,
    IMFMediaEngineClassFactoryVtbl,
    IMFMediaError,
    IMFTimedText,
    IMFTimedTextNotify,
    IMFTimedTextNotifyVtbl,
    IMFTimedTextTrack,
    IMFTimedTextTrackList,
    IMFTimedTextTrackListVtbl,
    IMFTimedTextTrackVtbl,
    IMFTimedTextVtbl,
    IUnknown,
    IUnknownVtbl,
    MF_ATTRIBUTE_TYPE,
    MF_ATTRIBUTES_MATCH_TYPE,
    MF_TIMED_TEXT_ERROR_CODE,
    MF_TIMED_TEXT_TRACK_KIND,
    PROPVARIANT,
    S_OK,
    _hr_ok,
)

# Media Foundation identifiers consumed by the runtime.
IID_IClassFactory = GUID(0x00000001, 0x0000, 0x0000, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46)
IID_IMFMediaEngineNotify_STR = "{FEE7C112-E776-42B5-9BBF-0048524E2BD5}"
IID_IMFMediaEngineNotify = GUID.from_string(IID_IMFMediaEngineNotify_STR)
MF_MEDIA_ENGINE_PLAYBACK_HWND = GUID(0xD988879B, 0x67C9, 0x4D92, 0xBA, 0xA7, 0x6E, 0xAD, 0xD4, 0x46, 0x03, 0x9D)
MF_MEDIA_ENGINE_CALLBACK = GUID(0xC60381B8, 0x83A4, 0x41F8, 0xA3, 0xD0, 0xDE, 0x05, 0x07, 0x68, 0x49, 0xA9)
MF_MEDIA_ENGINE_VIDEO_OUTPUT_FORMAT = GUID(0x5066893C, 0x8CF9, 0x42BC, 0x8B, 0x8A, 0x47, 0x22, 0x12, 0xE5, 0x27, 0x26)
MF_MEDIA_ENGINE_AUDIO_CATEGORY = GUID(0x4D8FAD4E, 0x0CFB, 0x434B, 0xA8, 0xEE, 0x80, 0xD0, 0x10, 0xA1, 0x5B, 0x5D)
DXGI_FORMAT_B8G8R8A8_UNORM = 87

# ---------------------------------------------------------------------------
# IMFMediaEngine VTable specs (index-based calls)
# ---------------------------------------------------------------------------

_IMF_MEDIA_ENGINE_VTBL_SPECS = {
    # Indici derivati da mfmediaengine.h (IUnknown + IMFMediaEngine).
    "SetSource": {"index": 6, "restype": HRESULT, "argtypes": [c_void_p]},  # BSTR
    "GetPreload": {"index": 9, "restype": c_uint32, "argtypes": []},
    "SetPreload": {"index": 10, "restype": HRESULT, "argtypes": [c_uint32]},
    "Load": {"index": 12, "restype": HRESULT, "argtypes": []},
    "GetCurrentTime": {"index": 16, "restype": c_double, "argtypes": []},
    "SetCurrentTime": {"index": 17, "restype": HRESULT, "argtypes": [c_double]},
    "GetDuration": {"index": 19, "restype": c_double, "argtypes": []},
    "GetEnded": {"index": 27, "restype": wintypes.BOOL, "argtypes": []},
    "IsEnded": {"index": 27, "restype": wintypes.BOOL, "argtypes": []},
    "GetAutoPlay": {"index": 28, "restype": wintypes.BOOL, "argtypes": []},
    "SetAutoPlay": {"index": 29, "restype": HRESULT, "argtypes": [wintypes.BOOL]},
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
