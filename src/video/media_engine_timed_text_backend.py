"""Owned, thread-affine Media Engine timed-text service access.

Cold operations are O(1); list enumeration is handled by the bounded consumer.
No COM call occurs under the core state lock. Each successful acquisition owns
one reference, even when successive calls return the same native address.
Missing services, a closing/replaced engine and invalid out-pointers fail closed.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Literal, Protocol, cast

from src.video.component_base.definitions_abi import (
    GUID, HRESULT, IMFMediaEngine, IMFTimedText, IMFTimedTextTrack,
    IMFTimedTextTrackList, IUnknown,
)
from src.video.component_base.definitions_runtime import _bind_function, _load_windll
from src.video.component_base.iid_registry import IID_IMFTimedText, MF_MEDIA_ENGINE_TIMEDTEXT
from .media_engine_core_shared import CoreState, MediaEngineError

TimedTextPointer = ctypes.POINTER(IMFTimedText)
TrackListPointer = ctypes.POINTER(IMFTimedTextTrackList)
TrackPointer = ctypes.POINTER(IMFTimedTextTrack)
OwnedPointer = TimedTextPointer | TrackListPointer | TrackPointer
_NATIVE_ERRORS = (OSError, ValueError, TypeError, ReferenceError, RuntimeError, ctypes.ArgumentError)
_MFGetService = _bind_function(
    _load_windll('mf.dll'), 'mf.dll', 'MFGetService',
    argtypes=[ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(GUID),
              ctypes.POINTER(ctypes.c_void_p)], restype=HRESULT,
)


class TimedTextBackendError(MediaEngineError):
    """Acquisition, ownership, affinity or a native method contract failed."""


class ComThreadIdentity(Protocol):
    def _is_on_com_thread(self) -> bool: ...


class TimedTextAdapter(Protocol):
    def _get_com_thread_manager(self, *, ensure_started: bool = True) -> ComThreadIdentity: ...


@dataclass(frozen=True)
class EngineSnapshot:
    address: int
    generation: int


def _require_context(core: CoreState) -> EngineSnapshot:
    """Reject foreign threads, dead adapters and shutdown before dereferencing COM."""
    adapter = core._adapter_ref()
    if adapter is None:
        raise TimedTextBackendError('Timed-text adapter has been released.')
    try:
        manager = cast(TimedTextAdapter, adapter)._get_com_thread_manager(ensure_started=False)
        on_thread = manager._is_on_com_thread()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise TimedTextBackendError(f'Timed-text COM thread unavailable: {exc}') from exc
    if on_thread is not True:
        raise TimedTextBackendError('Timed-text operation requires the adapter-owned COM thread.')
    with core._state_lock:
        engine = core._media_engine
        generation = getattr(core, '_engine_generation', None)
        closing = core._shutdown_requested
    if closing or not engine:
        raise TimedTextBackendError('Timed-text engine is closed or unavailable.')
    if not isinstance(engine, ctypes.POINTER(IMFMediaEngine)) or type(generation) is not int or generation < 0:
        raise TimedTextBackendError('Invalid engine pointer or generation contract.')
    address = ctypes.cast(engine, ctypes.c_void_p).value
    if address is None:
        raise TimedTextBackendError('Timed-text engine pointer is NULL.')
    return EngineSnapshot(address, generation)


def release_owned(pointer: OwnedPointer | None, context: str = 'timed-text reference') -> None:
    """Consume one acquired reference, with no address-based release suppression.

    NULL is harmless; unsupported types are rejected before access. Clear the
    owner before Release, including on a release failure. Cast aliases are NOT
    separate acquired references and must not be passed as new owners.
    """
    if pointer is None:
        return
    if not isinstance(pointer, (TimedTextPointer, TrackListPointer, TrackPointer)):
        raise TimedTextBackendError(f'{context}: expected an owned typed COM pointer.')
    address = ctypes.cast(pointer, ctypes.c_void_p).value
    if address is None:
        return
    try:
        unknown = ctypes.cast(address, ctypes.POINTER(IUnknown))
        if not unknown.contents.lpVtbl:
            raise TimedTextBackendError(f'{context}: NULL IUnknown vtable.')
        release = unknown.contents.lpVtbl.contents.Release
        if not release:
            raise TimedTextBackendError(f'{context}: NULL Release entry.')
        ctypes.cast(ctypes.byref(pointer), ctypes.POINTER(ctypes.c_void_p))[0] = None
        release(ctypes.c_void_p(address))  # Returned reference count is diagnostic, not HRESULT.
    except _NATIVE_ERRORS as exc:
        raise TimedTextBackendError(f'{context}: Release failed: {exc}') from exc


def _check_output(result: int, nonnull: bool, context: str) -> None:
    """Never dereference an out-pointer on failure, nor accept successful NULL."""
    if result & 0x80000000:
        suffix = ' with non-NULL output (ownership unknown)' if nonnull else ''
        raise TimedTextBackendError(f'{context} failed: HRESULT 0x{result & 0xFFFFFFFF:08X}{suffix}')
    if not nonnull:
        raise TimedTextBackendError(f'{context} succeeded with NULL output.')


@contextmanager
def timed_text_service(core: CoreState) -> Iterator[TimedTextPointer]:
    """Acquire through MFGetService; transfer/release only successful COM outputs.

    No speculative QueryInterface fallback or persistent service cache is used.
    Service absence remains an explicit error, and native availability is a
    Windows qualification requirement. Source/engine drift invalidates the call.
    """
    snapshot = _require_context(core)
    output = ctypes.c_void_p()
    result = int(_MFGetService(ctypes.c_void_p(snapshot.address), ctypes.byref(MF_MEDIA_ENGINE_TIMEDTEXT),
                               ctypes.byref(IID_IMFTimedText), ctypes.byref(output)))
    _check_output(result, output.value is not None, 'MFGetService(IMFTimedText)')
    service = ctypes.cast(output, TimedTextPointer)
    try:
        if _require_context(core) != snapshot:
            raise TimedTextBackendError('Engine changed during timed-text acquisition.')
        if not service.contents.lpVtbl:
            raise TimedTextBackendError('IMFTimedText returned a NULL vtable.')
        yield service
        if _require_context(core) != snapshot:
            raise TimedTextBackendError('Engine changed during timed-text operation.')
    finally:
        release_owned(service, 'IMFTimedText service')


def _get_track_list(core: CoreState, active: bool) -> TrackListPointer:
    """Return an owned list after service release; release the list if exit fails."""
    output = TrackListPointer()
    owned = False
    transferred = False
    try:
        with timed_text_service(core) as service:
            vtable = service.contents.lpVtbl.contents
            method = vtable.GetActiveTracks if active else vtable.GetTextTracks
            if not method:
                raise TimedTextBackendError('Timed-text list method is NULL.')
            result = int(method(service, ctypes.byref(output)))
            _check_output(result, bool(output), 'IMFTimedText list acquisition')
            owned = True
        transferred = True
        return output
    finally:
        if owned and not transferred:
            release_owned(output, 'untransferred timed-text list')


def _get_text_tracks_on_com_thread(self: CoreState) -> TrackListPointer:
    return _get_track_list(self, False)


def _get_active_timed_text_tracks_on_com_thread(self: CoreState) -> TrackListPointer:
    return _get_track_list(self, True)


def _call_timed_text_method_on_com_thread(
    self: CoreState, method: Literal['SelectTrack'], track_id: wintypes.DWORD,
    selected: wintypes.BOOL,
) -> int:
    """Dispatch only the typed selection operation; reject wraparound and unknown methods."""
    if method != 'SelectTrack':
        raise TimedTextBackendError('Unsupported timed-text operation; only SelectTrack is allowed.')
    if not isinstance(track_id, wintypes.DWORD) or not 0 <= track_id.value <= 0xFFFFFFFF:
        raise TimedTextBackendError('Track ID must be a DWORD in the uint32 range.')
    if not isinstance(selected, wintypes.BOOL) or selected.value not in (0, 1):
        raise TimedTextBackendError('Selected must be BOOL(0) or BOOL(1).')
    with timed_text_service(self) as service:
        select = service.contents.lpVtbl.contents.SelectTrack
        if not select:
            raise TimedTextBackendError('SelectTrack entry is NULL.')
        result = int(select(service, track_id, selected))
        if result & 0x80000000:
            raise TimedTextBackendError(f'SelectTrack({track_id.value}) failed: HRESULT 0x{result & 0xFFFFFFFF:08X}')
        return result


def attach_media_engine_timed_text_backend(cls: type) -> None:
    """Install real hook implementations on the canonical core, once."""
    if cls.__dict__.get('_timed_text_backend_attached', False):
        return
    cls._get_text_tracks_on_com_thread = _get_text_tracks_on_com_thread
    cls._get_active_timed_text_tracks_on_com_thread = _get_active_timed_text_tracks_on_com_thread
    cls._call_timed_text_method_on_com_thread = _call_timed_text_method_on_com_thread
    cls._timed_text_backend_attached = True
