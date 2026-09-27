# -*- coding: utf-8 -*-
"""ctypes declarations for WaveHelm's Windows Media Foundation ABI boundary.

This module is deliberately declarative. It owns the Windows-sized COM/Media
Foundation types and vtable layouts exported by ``definitions.py``. Runtime
loading, DLL ownership, logging, and Media Foundation lifecycle remain outside
this module.

Edge cases guarded here:
- non-Windows test collection must construct declarations without WinDLL;
- COM vtable order must remain byte-for-byte compatible with Windows headers;
- callback signatures must not silently drop parameters or methods.
"""

from __future__ import annotations

import ctypes
import uuid
from ctypes import (
    POINTER,
    Structure,
    Union,
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

_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
HRESULT = c_long
S_OK = 0


def _hr_ok(hr: int) -> bool:
    """Return whether an HRESULT represents success."""
    return hr >= 0


class GUID(Structure):
    """Windows GUID layout."""

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
    ) -> None:
        super().__init__()
        self.Data1 = c_uint32(d1)
        self.Data2 = c_uint16(d2)
        self.Data3 = c_uint16(d3)
        self.Data4 = (c_ubyte * 8)(b1, b2, b3, b4, b5, b6, b7, b8)

    @classmethod
    def from_string(cls, guid_str: str) -> "GUID":
        """Create a ctypes GUID from a canonical GUID string."""
        value = uuid.UUID(guid_str.strip("{}"))
        raw = value.bytes_le
        d1 = int.from_bytes(raw[0:4], "little")
        d2 = int.from_bytes(raw[4:6], "little")
        d3 = int.from_bytes(raw[6:8], "little")
        return cls(d1, d2, d3, *raw[8:16])

    def __repr__(self) -> str:
        d4 = bytes(self.Data4)
        return (
            f"{{{self.Data1:08X}-{self.Data2:04X}-{self.Data3:04X}-"
            f"{d4[0]:02X}{d4[1]:02X}-{d4[2]:02X}{d4[3]:02X}{d4[4]:02X}"
            f"{d4[5]:02X}{d4[6]:02X}{d4[7]:02X}}}"
        )


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
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("CreateInstance", _WINFUNCTYPE(HRESULT, c_void_p, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("LockServer", _WINFUNCTYPE(HRESULT, c_void_p, wintypes.BOOL)),
    ]


class IClassFactory(Structure):
    _fields_ = [("lpVtbl", POINTER(IClassFactoryVtbl))]


MF_ATTRIBUTE_TYPE = c_uint32
MF_ATTRIBUTES_MATCH_TYPE = c_uint32
_PROPVARIANT_RAW = (ctypes.c_ulonglong * 2) if ctypes.sizeof(c_void_p) == 8 else (ctypes.c_ulong * 2)


class _PROPVARIANT_UNION(Union):
    _fields_ = [
        ("llVal", ctypes.c_longlong),
        ("ullVal", ctypes.c_ulonglong),
        ("dblVal", c_double),
        ("pwszVal", wintypes.LPWSTR),
        ("punkVal", c_void_p),
        ("p", c_void_p),
        ("_raw", _PROPVARIANT_RAW),
    ]


class PROPVARIANT(Structure):
    """ABI-compatible PROPVARIANT storage used by IMFAttributes."""

    _fields_ = [
        ("vt", c_uint16),
        ("wReserved1", c_uint16),
        ("wReserved2", c_uint16),
        ("wReserved3", c_uint16),
        ("value", _PROPVARIANT_UNION),
    ]


class IMFAttributes(Structure):
    pass


class IMFMediaEngine(IUnknown):
    pass


class IMFMediaError(IUnknown):
    pass


class IMFMediaTimeRange(IUnknown):
    """Opaque COM output; only IUnknown operations are exposed here."""


class IMFByteStream(IUnknown):
    """Opaque COM input for timed-text source registration."""


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


class IMFTimedTextCue(Structure):
    pass


class IMFTimedTextCueList(IUnknown):
    """Opaque COM output; cue-list methods are outside this binding."""


MF_TIMED_TEXT_TRACK_READY_STATE = c_uint32
MF_TIMED_TEXT_TRACK_KIND = c_uint32
MF_TIMED_TEXT_ERROR_CODE = c_uint32
MF_TIMED_TEXT_CUE_EVENT = c_uint32


class IMFAttributesVtbl(Structure):
    _fields_ = [
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
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
        ("GetBlob", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_ubyte), c_uint32, POINTER(c_uint32))),
        ("GetAllocatedBlob", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(POINTER(c_ubyte)), POINTER(c_uint32))),
        ("GetUnknown", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(GUID), POINTER(c_void_p))),
        ("SetItem", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(PROPVARIANT))),
        ("DeleteItem", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID))),
        ("DeleteAllItems", _WINFUNCTYPE(HRESULT, c_void_p)),
        ("SetUINT32", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), c_uint32)),
        ("SetUINT64", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), c_uint64)),
        ("SetDouble", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), c_double)),
        ("SetGUID", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(GUID))),
        ("SetString", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), wintypes.LPCWSTR)),
        ("SetBlob", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_ubyte), c_uint32)),
        ("SetUnknown", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), c_void_p)),
        ("LockStore", _WINFUNCTYPE(HRESULT, c_void_p)),
        ("UnlockStore", _WINFUNCTYPE(HRESULT, c_void_p)),
        ("GetCount", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_uint32))),
        ("GetItemByIndex", _WINFUNCTYPE(HRESULT, c_void_p, c_uint32, POINTER(GUID), POINTER(PROPVARIANT))),
        ("CopyAllItems", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(IMFAttributes))),
    ]


IMFAttributes._fields_ = [("lpVtbl", POINTER(IMFAttributesVtbl))]


class IMFDXGIDeviceManagerVtbl(Structure):
    _fields_ = [
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("CloseDeviceHandle", _WINFUNCTYPE(HRESULT, c_void_p, c_void_p)),
        ("GetVideoService", _WINFUNCTYPE(HRESULT, c_void_p, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("LockDevice", _WINFUNCTYPE(HRESULT, c_void_p, c_void_p, POINTER(GUID), POINTER(c_void_p), wintypes.BOOL)),
        ("OpenDeviceHandle", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_void_p))),
        ("ResetDevice", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(IUnknown), c_uint32)),
        ("TestDevice", _WINFUNCTYPE(HRESULT, c_void_p, c_void_p)),
        ("UnlockDevice", _WINFUNCTYPE(HRESULT, c_void_p, c_void_p, wintypes.BOOL)),
    ]


class IMFTimedTextNotifyVtbl(Structure):
    _fields_ = [
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("TrackAdded", _WINFUNCTYPE(None, c_void_p, wintypes.DWORD)),
        ("TrackRemoved", _WINFUNCTYPE(None, c_void_p, wintypes.DWORD)),
        ("TrackSelected", _WINFUNCTYPE(None, c_void_p, wintypes.DWORD, wintypes.BOOL)),
        ("TrackReadyStateChanged", _WINFUNCTYPE(None, c_void_p, wintypes.DWORD)),
        ("Error", _WINFUNCTYPE(None, c_void_p, MF_TIMED_TEXT_ERROR_CODE, HRESULT, wintypes.DWORD)),
        ("Cue", _WINFUNCTYPE(None, c_void_p, MF_TIMED_TEXT_CUE_EVENT, c_double, POINTER(IMFTimedTextCue))),
        ("Reset", _WINFUNCTYPE(None, c_void_p)),
    ]


class IMFTimedTextTrackVtbl(Structure):
    # Declaration order from mfmediaengine.h, NOT the documentation method index.
    _fields_ = [
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("GetId", _WINFUNCTYPE(wintypes.DWORD, c_void_p)),
        ("GetLabel", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(wintypes.LPWSTR))),
        ("SetLabel", _WINFUNCTYPE(HRESULT, c_void_p, wintypes.LPCWSTR)),
        ("GetLanguage", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(wintypes.LPWSTR))),
        ("GetTrackKind", _WINFUNCTYPE(MF_TIMED_TEXT_TRACK_KIND, c_void_p)),
        ("IsInBand", _WINFUNCTYPE(wintypes.BOOL, c_void_p)),
        ("GetInBandMetadataTrackDispatchType", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(wintypes.LPWSTR))),
        ("IsActive", _WINFUNCTYPE(wintypes.BOOL, c_void_p)),
        ("GetErrorCode", _WINFUNCTYPE(MF_TIMED_TEXT_ERROR_CODE, c_void_p)),
        ("GetExtendedErrorCode", _WINFUNCTYPE(HRESULT, c_void_p)),
        ("GetDataFormat", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID))),
        ("GetReadyState", _WINFUNCTYPE(MF_TIMED_TEXT_TRACK_READY_STATE, c_void_p)),
        ("GetCueList", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(POINTER(IMFTimedTextCueList)))),
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
    # Declaration order from mfmediaengine.h, NOT the documentation method index.
    _fields_ = [
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("RegisterNotifications", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(IMFTimedTextNotify))),
        ("SelectTrack", _WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD, wintypes.BOOL)),
        ("AddDataSource", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(IMFByteStream), wintypes.LPCWSTR, wintypes.LPCWSTR, MF_TIMED_TEXT_TRACK_KIND, wintypes.BOOL, POINTER(wintypes.DWORD))),
        ("AddDataSourceFromUrl", _WINFUNCTYPE(HRESULT, c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, MF_TIMED_TEXT_TRACK_KIND, wintypes.BOOL, POINTER(wintypes.DWORD))),
        ("AddTrack", _WINFUNCTYPE(HRESULT, c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR, MF_TIMED_TEXT_TRACK_KIND, POINTER(POINTER(IMFTimedTextTrack)))),
        ("RemoveTrack", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(IMFTimedTextTrack))),
        ("GetCueTimeOffset", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_double))),
        ("SetCueTimeOffset", _WINFUNCTYPE(HRESULT, c_void_p, c_double)),
        ("GetTracks", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(POINTER(IMFTimedTextTrackList)))),
        ("GetActiveTracks", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(POINTER(IMFTimedTextTrackList)))),
        ("GetTextTracks", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(POINTER(IMFTimedTextTrackList)))),
        ("GetMetadataTracks", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(POINTER(IMFTimedTextTrackList)))),
        ("SetInBandEnabled", _WINFUNCTYPE(HRESULT, c_void_p, wintypes.BOOL)),
        ("IsInBandEnabled", _WINFUNCTYPE(wintypes.BOOL, c_void_p)),
    ]


IMFTimedTextNotify._fields_ = [("lpVtbl", POINTER(IMFTimedTextNotifyVtbl))]
IMFTimedTextTrack._fields_ = [("lpVtbl", POINTER(IMFTimedTextTrackVtbl))]
IMFTimedTextTrackList._fields_ = [("lpVtbl", POINTER(IMFTimedTextTrackListVtbl))]
IMFTimedText._fields_ = [("lpVtbl", POINTER(IMFTimedTextVtbl))]


class IMFMediaEngineClassFactoryVtbl(Structure):
    # Declaration order from mfmediaengine.h, NOT the documentation method index.
    _fields_ = [
        ("QueryInterface", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("Release", _WINFUNCTYPE(c_ulong, c_void_p)),
        ("CreateInstance", _WINFUNCTYPE(HRESULT, c_void_p, c_uint32, POINTER(IMFAttributes), POINTER(POINTER(IMFMediaEngine)))),
        ("CreateTimeRange", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(POINTER(IMFMediaTimeRange)))),
        ("CreateError", _WINFUNCTYPE(HRESULT, c_void_p, POINTER(POINTER(IMFMediaError)))),
    ]


class IMFMediaEngineClassFactory(Structure):
    _fields_ = [("lpVtbl", POINTER(IMFMediaEngineClassFactoryVtbl))]
