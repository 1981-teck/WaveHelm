#define CINTERFACE
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mfidl.h>
#include <mfobjects.h>
#include <mfmediaengine.h>

#include <cstddef>
#include <cstdio>
#include <type_traits>

using LockDeviceSignature = HRESULT(STDMETHODCALLTYPE *)(
    IMFDXGIDeviceManager *, HANDLE, REFIID, void **, BOOL);
using TrackAddedSignature = void(STDMETHODCALLTYPE *)(IMFTimedTextNotify *, DWORD);
using TrackSelectedSignature = void(STDMETHODCALLTYPE *)(IMFTimedTextNotify *, DWORD, BOOL);
using TrackReadySignature = void(STDMETHODCALLTYPE *)(IMFTimedTextNotify *, DWORD);
using TimedTextErrorSignature = void(STDMETHODCALLTYPE *)(
    IMFTimedTextNotify *, MF_TIMED_TEXT_ERROR_CODE, HRESULT, DWORD);
using TimedTextCueSignature = void(STDMETHODCALLTYPE *)(
    IMFTimedTextNotify *, MF_TIMED_TEXT_CUE_EVENT, double, IMFTimedTextCue *);
using TimedTextResetSignature = void(STDMETHODCALLTYPE *)(IMFTimedTextNotify *);

static_assert(std::is_same_v<decltype(((IMFDXGIDeviceManagerVtbl *)nullptr)->LockDevice), LockDeviceSignature>);
static_assert(std::is_same_v<decltype(((IMFTimedTextNotifyVtbl *)nullptr)->TrackAdded), TrackAddedSignature>);
static_assert(std::is_same_v<decltype(((IMFTimedTextNotifyVtbl *)nullptr)->TrackRemoved), TrackAddedSignature>);
static_assert(std::is_same_v<decltype(((IMFTimedTextNotifyVtbl *)nullptr)->TrackSelected), TrackSelectedSignature>);
static_assert(std::is_same_v<decltype(((IMFTimedTextNotifyVtbl *)nullptr)->TrackReadyStateChanged), TrackReadySignature>);
static_assert(std::is_same_v<decltype(((IMFTimedTextNotifyVtbl *)nullptr)->Error), TimedTextErrorSignature>);
static_assert(std::is_same_v<decltype(((IMFTimedTextNotifyVtbl *)nullptr)->Cue), TimedTextCueSignature>);
static_assert(std::is_same_v<decltype(((IMFTimedTextNotifyVtbl *)nullptr)->Reset), TimedTextResetSignature>);

// All three repaired vtables: exact SDK function signatures and complete slot counts.
// The Python signature contracts are checked independently in the test suite.
#define CHECK_METHOD(TABLE, NAME, ...) \
    static_assert(std::is_same_v<decltype(((TABLE *)nullptr)->NAME), __VA_ARGS__>, #TABLE "::" #NAME)
static_assert(sizeof(void *) == 8, "WaveHelm qualification targets Windows x64");
static_assert(sizeof(IMFTimedTextTrackVtbl) == 16 * sizeof(void *));
CHECK_METHOD(IMFTimedTextTrackVtbl, QueryInterface, HRESULT(STDMETHODCALLTYPE *)(IMFTimedTextTrack *, REFIID, void **));
CHECK_METHOD(IMFTimedTextTrackVtbl, AddRef, ULONG(STDMETHODCALLTYPE *)(IMFTimedTextTrack *));
CHECK_METHOD(IMFTimedTextTrackVtbl, Release, ULONG(STDMETHODCALLTYPE *)(IMFTimedTextTrack *));
CHECK_METHOD(IMFTimedTextTrackVtbl, GetId, DWORD(STDMETHODCALLTYPE *)(IMFTimedTextTrack *));
CHECK_METHOD(IMFTimedTextTrackVtbl, GetLabel, HRESULT(STDMETHODCALLTYPE *)(IMFTimedTextTrack *, LPWSTR *));
CHECK_METHOD(IMFTimedTextTrackVtbl, SetLabel, HRESULT(STDMETHODCALLTYPE *)(IMFTimedTextTrack *, LPCWSTR));
CHECK_METHOD(IMFTimedTextTrackVtbl, GetLanguage, HRESULT(STDMETHODCALLTYPE *)(IMFTimedTextTrack *, LPWSTR *));
CHECK_METHOD(IMFTimedTextTrackVtbl, GetTrackKind, MF_TIMED_TEXT_TRACK_KIND(STDMETHODCALLTYPE *)(IMFTimedTextTrack *));
CHECK_METHOD(IMFTimedTextTrackVtbl, IsInBand, BOOL(STDMETHODCALLTYPE *)(IMFTimedTextTrack *));
CHECK_METHOD(IMFTimedTextTrackVtbl, GetInBandMetadataTrackDispatchType, HRESULT(STDMETHODCALLTYPE *)(IMFTimedTextTrack *, LPWSTR *));
CHECK_METHOD(IMFTimedTextTrackVtbl, IsActive, BOOL(STDMETHODCALLTYPE *)(IMFTimedTextTrack *));
CHECK_METHOD(IMFTimedTextTrackVtbl, GetErrorCode, MF_TIMED_TEXT_ERROR_CODE(STDMETHODCALLTYPE *)(IMFTimedTextTrack *));
CHECK_METHOD(IMFTimedTextTrackVtbl, GetExtendedErrorCode, HRESULT(STDMETHODCALLTYPE *)(IMFTimedTextTrack *));
CHECK_METHOD(IMFTimedTextTrackVtbl, GetDataFormat, HRESULT(STDMETHODCALLTYPE *)(IMFTimedTextTrack *, GUID *));
CHECK_METHOD(IMFTimedTextTrackVtbl, GetReadyState, MF_TIMED_TEXT_TRACK_READY_STATE(STDMETHODCALLTYPE *)(IMFTimedTextTrack *));
CHECK_METHOD(IMFTimedTextTrackVtbl, GetCueList, HRESULT(STDMETHODCALLTYPE *)(IMFTimedTextTrack *, IMFTimedTextCueList **));

static_assert(sizeof(IMFTimedTextVtbl) == 17 * sizeof(void *));
CHECK_METHOD(IMFTimedTextVtbl, QueryInterface, HRESULT(STDMETHODCALLTYPE *)(IMFTimedText *, REFIID, void **));
CHECK_METHOD(IMFTimedTextVtbl, AddRef, ULONG(STDMETHODCALLTYPE *)(IMFTimedText *));
CHECK_METHOD(IMFTimedTextVtbl, Release, ULONG(STDMETHODCALLTYPE *)(IMFTimedText *));
CHECK_METHOD(IMFTimedTextVtbl, RegisterNotifications, HRESULT(STDMETHODCALLTYPE *)(IMFTimedText *, IMFTimedTextNotify *));
CHECK_METHOD(IMFTimedTextVtbl, SelectTrack, HRESULT(STDMETHODCALLTYPE *)(IMFTimedText *, DWORD, BOOL));
CHECK_METHOD(IMFTimedTextVtbl, AddDataSource, HRESULT(STDMETHODCALLTYPE *)(IMFTimedText *, IMFByteStream *, LPCWSTR, LPCWSTR, MF_TIMED_TEXT_TRACK_KIND, BOOL, DWORD *));
CHECK_METHOD(IMFTimedTextVtbl, AddDataSourceFromUrl, HRESULT(STDMETHODCALLTYPE *)(IMFTimedText *, LPCWSTR, LPCWSTR, LPCWSTR, MF_TIMED_TEXT_TRACK_KIND, BOOL, DWORD *));
CHECK_METHOD(IMFTimedTextVtbl, AddTrack, HRESULT(STDMETHODCALLTYPE *)(IMFTimedText *, LPCWSTR, LPCWSTR, MF_TIMED_TEXT_TRACK_KIND, IMFTimedTextTrack **));
CHECK_METHOD(IMFTimedTextVtbl, RemoveTrack, HRESULT(STDMETHODCALLTYPE *)(IMFTimedText *, IMFTimedTextTrack *));
CHECK_METHOD(IMFTimedTextVtbl, GetCueTimeOffset, HRESULT(STDMETHODCALLTYPE *)(IMFTimedText *, double *));
CHECK_METHOD(IMFTimedTextVtbl, SetCueTimeOffset, HRESULT(STDMETHODCALLTYPE *)(IMFTimedText *, double));
CHECK_METHOD(IMFTimedTextVtbl, GetTracks, HRESULT(STDMETHODCALLTYPE *)(IMFTimedText *, IMFTimedTextTrackList **));
CHECK_METHOD(IMFTimedTextVtbl, GetActiveTracks, HRESULT(STDMETHODCALLTYPE *)(IMFTimedText *, IMFTimedTextTrackList **));
CHECK_METHOD(IMFTimedTextVtbl, GetTextTracks, HRESULT(STDMETHODCALLTYPE *)(IMFTimedText *, IMFTimedTextTrackList **));
CHECK_METHOD(IMFTimedTextVtbl, GetMetadataTracks, HRESULT(STDMETHODCALLTYPE *)(IMFTimedText *, IMFTimedTextTrackList **));
CHECK_METHOD(IMFTimedTextVtbl, SetInBandEnabled, HRESULT(STDMETHODCALLTYPE *)(IMFTimedText *, BOOL));
CHECK_METHOD(IMFTimedTextVtbl, IsInBandEnabled, BOOL(STDMETHODCALLTYPE *)(IMFTimedText *));

static_assert(sizeof(IMFMediaEngineClassFactoryVtbl) == 6 * sizeof(void *));
CHECK_METHOD(IMFMediaEngineClassFactoryVtbl, QueryInterface, HRESULT(STDMETHODCALLTYPE *)(IMFMediaEngineClassFactory *, REFIID, void **));
CHECK_METHOD(IMFMediaEngineClassFactoryVtbl, AddRef, ULONG(STDMETHODCALLTYPE *)(IMFMediaEngineClassFactory *));
CHECK_METHOD(IMFMediaEngineClassFactoryVtbl, Release, ULONG(STDMETHODCALLTYPE *)(IMFMediaEngineClassFactory *));
CHECK_METHOD(IMFMediaEngineClassFactoryVtbl, CreateInstance, HRESULT(STDMETHODCALLTYPE *)(IMFMediaEngineClassFactory *, DWORD, IMFAttributes *, IMFMediaEngine **));
CHECK_METHOD(IMFMediaEngineClassFactoryVtbl, CreateTimeRange, HRESULT(STDMETHODCALLTYPE *)(IMFMediaEngineClassFactory *, IMFMediaTimeRange **));
CHECK_METHOD(IMFMediaEngineClassFactoryVtbl, CreateError, HRESULT(STDMETHODCALLTYPE *)(IMFMediaEngineClassFactory *, IMFMediaError **));


static void print_offset(const char *name, size_t value, bool last = false) {
    std::printf("\"%s\":%zu%s", name, value, last ? "" : ",");
}

#define BEGIN_TYPE(TYPE) \
    std::printf("\"" #TYPE "\":{\"size\":%zu,\"align\":%zu,\"offsets\":{", sizeof(TYPE), alignof(TYPE))
#define FIELD(TYPE, NAME) print_offset(#NAME, offsetof(TYPE, NAME))
#define LAST_FIELD(TYPE, NAME) print_offset(#NAME, offsetof(TYPE, NAME), true)
#define END_TYPE(LAST) std::printf("}}%s", LAST ? "" : ",")

static void print_core_types() {
    static_assert(sizeof(GUID) == 16);
    static_assert(alignof(GUID) == 4);
    static_assert(offsetof(GUID, Data1) == 0);
    static_assert(offsetof(GUID, Data2) == 4);
    static_assert(offsetof(GUID, Data3) == 6);
    static_assert(offsetof(GUID, Data4) == 8);

    std::printf("{\"schema\":\"wavehelm-windows-abi-reference-v1\",");
    std::printf("\"pointer_size\":%zu,\"types\":{", sizeof(void *));

    BEGIN_TYPE(GUID);
    FIELD(GUID, Data1); FIELD(GUID, Data2); FIELD(GUID, Data3); LAST_FIELD(GUID, Data4);
    END_TYPE(false);

    BEGIN_TYPE(PROPVARIANT);
    FIELD(PROPVARIANT, vt); FIELD(PROPVARIANT, wReserved1);
    FIELD(PROPVARIANT, wReserved2); FIELD(PROPVARIANT, wReserved3);
    print_offset("value", offsetof(PROPVARIANT, punkVal), true);
    END_TYPE(false);

    BEGIN_TYPE(IMFAttributesVtbl);
    FIELD(IMFAttributesVtbl, QueryInterface); FIELD(IMFAttributesVtbl, AddRef);
    FIELD(IMFAttributesVtbl, Release); FIELD(IMFAttributesVtbl, GetItem);
    FIELD(IMFAttributesVtbl, GetItemType); FIELD(IMFAttributesVtbl, CompareItem);
    FIELD(IMFAttributesVtbl, Compare); FIELD(IMFAttributesVtbl, GetUINT32);
    FIELD(IMFAttributesVtbl, GetUINT64); FIELD(IMFAttributesVtbl, GetDouble);
    FIELD(IMFAttributesVtbl, GetGUID); FIELD(IMFAttributesVtbl, GetStringLength);
    FIELD(IMFAttributesVtbl, GetString); FIELD(IMFAttributesVtbl, GetAllocatedString);
    FIELD(IMFAttributesVtbl, GetBlobSize); FIELD(IMFAttributesVtbl, GetBlob);
    FIELD(IMFAttributesVtbl, GetAllocatedBlob); FIELD(IMFAttributesVtbl, GetUnknown);
    FIELD(IMFAttributesVtbl, SetItem); FIELD(IMFAttributesVtbl, DeleteItem);
    FIELD(IMFAttributesVtbl, DeleteAllItems); FIELD(IMFAttributesVtbl, SetUINT32);
    FIELD(IMFAttributesVtbl, SetUINT64); FIELD(IMFAttributesVtbl, SetDouble);
    FIELD(IMFAttributesVtbl, SetGUID); FIELD(IMFAttributesVtbl, SetString);
    FIELD(IMFAttributesVtbl, SetBlob); FIELD(IMFAttributesVtbl, SetUnknown);
    FIELD(IMFAttributesVtbl, LockStore); FIELD(IMFAttributesVtbl, UnlockStore);
    FIELD(IMFAttributesVtbl, GetCount); FIELD(IMFAttributesVtbl, GetItemByIndex);
    LAST_FIELD(IMFAttributesVtbl, CopyAllItems);
    END_TYPE(false);

}

static void print_device_and_notify() {
    BEGIN_TYPE(IMFDXGIDeviceManagerVtbl);
    FIELD(IMFDXGIDeviceManagerVtbl, QueryInterface); FIELD(IMFDXGIDeviceManagerVtbl, AddRef);
    FIELD(IMFDXGIDeviceManagerVtbl, Release); FIELD(IMFDXGIDeviceManagerVtbl, CloseDeviceHandle);
    FIELD(IMFDXGIDeviceManagerVtbl, GetVideoService); FIELD(IMFDXGIDeviceManagerVtbl, LockDevice);
    FIELD(IMFDXGIDeviceManagerVtbl, OpenDeviceHandle); FIELD(IMFDXGIDeviceManagerVtbl, ResetDevice);
    FIELD(IMFDXGIDeviceManagerVtbl, TestDevice); LAST_FIELD(IMFDXGIDeviceManagerVtbl, UnlockDevice);
    END_TYPE(false);

    BEGIN_TYPE(IMFTimedTextNotifyVtbl);
    FIELD(IMFTimedTextNotifyVtbl, QueryInterface); FIELD(IMFTimedTextNotifyVtbl, AddRef);
    FIELD(IMFTimedTextNotifyVtbl, Release); FIELD(IMFTimedTextNotifyVtbl, TrackAdded);
    FIELD(IMFTimedTextNotifyVtbl, TrackRemoved); FIELD(IMFTimedTextNotifyVtbl, TrackSelected);
    FIELD(IMFTimedTextNotifyVtbl, TrackReadyStateChanged); FIELD(IMFTimedTextNotifyVtbl, Error);
    FIELD(IMFTimedTextNotifyVtbl, Cue); LAST_FIELD(IMFTimedTextNotifyVtbl, Reset);
    END_TYPE(false);

}

static void print_tracks() {
    BEGIN_TYPE(IMFTimedTextTrackVtbl);
    FIELD(IMFTimedTextTrackVtbl, QueryInterface);
    FIELD(IMFTimedTextTrackVtbl, AddRef);
    FIELD(IMFTimedTextTrackVtbl, Release);
    FIELD(IMFTimedTextTrackVtbl, GetId);
    FIELD(IMFTimedTextTrackVtbl, GetLabel);
    FIELD(IMFTimedTextTrackVtbl, SetLabel);
    FIELD(IMFTimedTextTrackVtbl, GetLanguage);
    FIELD(IMFTimedTextTrackVtbl, GetTrackKind);
    FIELD(IMFTimedTextTrackVtbl, IsInBand);
    FIELD(IMFTimedTextTrackVtbl, GetInBandMetadataTrackDispatchType);
    FIELD(IMFTimedTextTrackVtbl, IsActive);
    FIELD(IMFTimedTextTrackVtbl, GetErrorCode);
    FIELD(IMFTimedTextTrackVtbl, GetExtendedErrorCode);
    FIELD(IMFTimedTextTrackVtbl, GetDataFormat);
    FIELD(IMFTimedTextTrackVtbl, GetReadyState);
    LAST_FIELD(IMFTimedTextTrackVtbl, GetCueList);
    END_TYPE(false);

    BEGIN_TYPE(IMFTimedTextTrackListVtbl);
    FIELD(IMFTimedTextTrackListVtbl, QueryInterface); FIELD(IMFTimedTextTrackListVtbl, AddRef);
    FIELD(IMFTimedTextTrackListVtbl, Release); FIELD(IMFTimedTextTrackListVtbl, GetLength);
    FIELD(IMFTimedTextTrackListVtbl, GetTrack); LAST_FIELD(IMFTimedTextTrackListVtbl, GetTrackById);
    END_TYPE(false);

}

static void print_text_and_factory() {
    BEGIN_TYPE(IMFTimedTextVtbl);
    FIELD(IMFTimedTextVtbl, QueryInterface);
    FIELD(IMFTimedTextVtbl, AddRef);
    FIELD(IMFTimedTextVtbl, Release);
    FIELD(IMFTimedTextVtbl, RegisterNotifications);
    FIELD(IMFTimedTextVtbl, SelectTrack);
    FIELD(IMFTimedTextVtbl, AddDataSource);
    FIELD(IMFTimedTextVtbl, AddDataSourceFromUrl);
    FIELD(IMFTimedTextVtbl, AddTrack);
    FIELD(IMFTimedTextVtbl, RemoveTrack);
    FIELD(IMFTimedTextVtbl, GetCueTimeOffset);
    FIELD(IMFTimedTextVtbl, SetCueTimeOffset);
    FIELD(IMFTimedTextVtbl, GetTracks);
    FIELD(IMFTimedTextVtbl, GetActiveTracks);
    FIELD(IMFTimedTextVtbl, GetTextTracks);
    FIELD(IMFTimedTextVtbl, GetMetadataTracks);
    FIELD(IMFTimedTextVtbl, SetInBandEnabled);
    LAST_FIELD(IMFTimedTextVtbl, IsInBandEnabled);
    END_TYPE(false);

    BEGIN_TYPE(IMFMediaEngineClassFactoryVtbl);
    FIELD(IMFMediaEngineClassFactoryVtbl, QueryInterface);
    FIELD(IMFMediaEngineClassFactoryVtbl, AddRef);
    FIELD(IMFMediaEngineClassFactoryVtbl, Release);
    FIELD(IMFMediaEngineClassFactoryVtbl, CreateInstance);
    FIELD(IMFMediaEngineClassFactoryVtbl, CreateTimeRange);
    LAST_FIELD(IMFMediaEngineClassFactoryVtbl, CreateError);
    END_TYPE(true);

}

int main() {
    print_core_types();
    print_device_and_notify();
    print_tracks();
    print_text_and_factory();
    std::printf("}}\n");
    return std::ferror(stdout) ? 1 : 0;
}
