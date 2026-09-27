"""Cold timed-text behavior extracted from playback without changing its public API.

The state lock guards only state snapshots. COM operations remain outside it and
on the adapter-owned thread. Compatibility exports live in playback. Acquisition
uses the real service backend; GUI queries retain their logged empty-result API.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import byref, wintypes
from typing import Literal, Tuple, TypedDict

from src.video.component_base.com_helpers import _hr_to_hex
from src.video.component_base.definitions import IMFTimedTextTrack, IMFTimedTextTrackList
from src.video.component_base.definitions_runtime import _bind_function, _ole32
from src.video.component_base.utils import is_success
from .media_engine_timed_text_backend import (
    TimedTextBackendError, EngineSnapshot, _require_context,
    release_owned as safe_release,
)
from .media_engine_core_shared import (
    MediaEngineError, CoreState,
    CORE_PLAYBACK_EXCEPTIONS, QUERY_EXCEPTIONS,
)

logger = logging.getLogger(__name__)


# COM public LPWSTR out-parameters use the task allocator, not the BSTR allocator.
_CoTaskMemFree = _bind_function(
    _ole32, 'ole32', 'CoTaskMemFree', argtypes=[ctypes.c_void_p], restype=None,
)
MAX_TIMED_TEXT_UNITS = 32768
MAX_TIMED_TEXT_TRACKS = 1024
TrackPointer = ctypes.POINTER(IMFTimedTextTrack)
TrackListPointer = ctypes.POINTER(IMFTimedTextTrackList)


class TimedTextMemoryError(MediaEngineError):
    """The native timed-text ownership or string-copy contract failed."""


class TimedTextUnavailableError(MediaEngineError):
    """The current backend has no real timed-text acquisition route."""


class TrackDescriptor(TypedDict):
    """Copied, pointer-free metadata crossing back to the controller."""

    track_id: int
    kind: int
    kind_label: str
    language: str
    label: str
    raw_label: str
    is_active: bool
    is_in_band: bool


def _copy_task_wstr(address: int) -> str:
    """Copy a valid, NUL-terminated owned buffer with a fixed work bound.

    The COM provider must return valid memory. This is not a safe reader for an
    arbitrary address. Empty strings, Unicode and over-budget strings are tested.
    Complexity is O(n), n <= MAX_TIMED_TEXT_UNITS; no lock is held.
    """
    pointer = ctypes.cast(address, ctypes.POINTER(ctypes.c_wchar))
    for count in range(MAX_TIMED_TEXT_UNITS + 1):
        if pointer[count] == '\0':
            return ctypes.wstring_at(address, count).strip()
    raise TimedTextMemoryError('Timed-text string exceeds the 32768-unit limit.')


def _decode_timed_text_wstr(raw_value: wintypes.LPWSTR | None) -> str:
    """Consume one successful COM task-memory LPWSTR out-parameter exactly once.

    NULL needs no release. Python strings and untyped pointers are rejected.
    A copy/limit failure still releases the allocation; release failures propagate.
    Clear the owner before release so a repeated call cannot free the same pointer.
    """
    if raw_value is None:
        return ''
    if not isinstance(raw_value, ctypes.c_wchar_p):
        raise TypeError('Expected an owned LPWSTR from a successful COM getter.')
    address = ctypes.cast(raw_value, ctypes.c_void_p).value
    if address is None:
        return ''
    try:
        try:
            return _copy_task_wstr(address)
        except (OSError, ValueError, RuntimeError, ctypes.ArgumentError) as exc:
            raise TimedTextMemoryError(f'Timed-text copy failed: {exc}') from exc
    finally:
        raw_value.value = None
        try:
            _CoTaskMemFree(ctypes.c_void_p(address))
        except (OSError, ValueError, RuntimeError, ctypes.ArgumentError) as exc:
            raise TimedTextMemoryError(f'Timed-text release failed: {exc}') from exc


def _read_track_string(track: TrackPointer, field: Literal['GetLabel', 'GetLanguage']) -> str:
    """Read optional metadata under the public COM out-parameter contract.

    A failed HRESULT with NULL is absent metadata, not a successful getter.
    A failure with a non-NULL pointer violates COM rules: do not dereference or
    guess ownership; raise and preserve the original failure for diagnosis.
    Successful NULL, empty allocated strings and decode failures are distinct.
    """
    owner = wintypes.LPWSTR()
    try:
        result = int(getattr(track.contents.lpVtbl.contents, field)(track, byref(owner)))
    except CORE_PLAYBACK_EXCEPTIONS as exc:
        raise TimedTextMemoryError(f'{field} failed before ownership transfer: {exc}') from exc
    if not is_success(result):
        if ctypes.cast(owner, ctypes.c_void_p).value is not None:
            raise TimedTextMemoryError(f'{field} failed with a non-NULL out-pointer; ownership unknown.')
        logger.debug('%s unavailable: HRESULT %s', field, _hr_to_hex(result))
        return ''
    return _decode_timed_text_wstr(owner)


def _read_timed_text_track_descriptor_on_com_thread(
    self: CoreState, track_ptr: TrackPointer,
) -> TrackDescriptor | None:
    """Copy track metadata on the COM thread, without retaining native strings.

    A NULL track has no descriptor. Failed optional strings keep deterministic
    labels, while invalid ownership/decoding aborts instead of reporting success.
    The caller owns and releases the acquired COM track reference.
    """
    if not track_ptr:
        return None
    vtbl = track_ptr.contents.lpVtbl.contents
    track_id = int(vtbl.GetId(track_ptr))
    kind = int(vtbl.GetTrackKind(track_ptr))
    active = bool(int(vtbl.IsActive(track_ptr)))
    in_band = bool(int(vtbl.IsInBand(track_ptr)))
    language = _read_track_string(track_ptr, 'GetLanguage')
    label = _read_track_string(track_ptr, 'GetLabel')
    return TrackDescriptor(
        track_id=track_id, kind=kind,
        kind_label='unknown' if kind == 0 else f'kind-{kind}',
        language=language, label=label or language or f'Subtitle {track_id}',
        raw_label=label, is_active=active, is_in_band=in_band,
    )


def _acquire_track_descriptor(self: CoreState, tracks: TrackListPointer, index: int) -> TrackDescriptor:
    """Reject failed/NULL getters; release each successfully acquired track once."""
    owner = TrackPointer()
    hr = int(tracks.contents.lpVtbl.contents.GetTrack(tracks, wintypes.DWORD(index), byref(owner)))
    if not is_success(hr):
        suffix = ' with non-NULL output; ownership unknown' if owner else ''
        raise TimedTextBackendError(f'GetTrack({index}) failed: {_hr_to_hex(hr)}{suffix}')
    if not owner:
        raise TimedTextBackendError(f'GetTrack({index}) succeeded with a NULL pointer.')
    try:
        descriptor = _read_timed_text_track_descriptor_on_com_thread(self, owner)
        if descriptor is None:
            raise TimedTextBackendError(f'GetTrack({index}) has no descriptor.')
        return descriptor
    finally:
        safe_release(owner, f'timed text track {index}')


def _enumerate_timed_text_track_list_on_com_thread(
    self: CoreState, track_list_ptr: TrackListPointer,
) -> Tuple[TrackDescriptor, ...]:
    """Copy a complete bounded list, never a silently shortened failure result.

    Reject failed/null entries, duplicate IDs, negative/excessive counts and a
    length change during enumeration. This is not an atomic provider snapshot;
    same-length concurrent replacement cannot be proven absent by this API.
    """
    if not track_list_ptr:
        return ()
    descriptors: list[TrackDescriptor] = []
    seen: set[int] = set()
    vtbl = track_list_ptr.contents.lpVtbl.contents
    track_count = int(vtbl.GetLength(track_list_ptr))
    if not 0 <= track_count <= MAX_TIMED_TEXT_TRACKS:
        raise TimedTextMemoryError('Timed-text track count violates the 1024-track limit.')
    for index in range(track_count):
        descriptor = _acquire_track_descriptor(self, track_list_ptr, index)
        track_id = descriptor['track_id']
        if type(track_id) is not int or not 0 <= track_id <= 0xFFFFFFFF or track_id in seen:
            raise TimedTextBackendError('Timed-text list has an invalid or duplicate track ID.')
        seen.add(track_id)
        descriptors.append(descriptor)
    if int(vtbl.GetLength(track_list_ptr)) != track_count:
        raise TimedTextBackendError('Timed-text list length changed during enumeration.')
    return tuple(descriptors)



def _get_text_track_descriptors_on_com_thread(self: CoreState) -> Tuple[TrackDescriptor, ...]:
    """Return timed-text descriptors directly from the COM thread without nested adapter dispatch.

    Edge cases:
        1. A missing backend getter must be an explicit unsupported capability, not a successful empty query.
        2. Getter failures must not leak partially enumerated COM pointers.
        3. Teardown can null the engine while callers are already on the COM thread, so state must be revalidated.
    """
    with self._state_lock:
        if self._shutdown_requested or not self._media_engine:
            return ()
    get_tracks = getattr(self, '_get_text_tracks_on_com_thread', None)
    if not callable(get_tracks):
        raise TimedTextUnavailableError('Timed-text acquisition is not implemented by this backend.')
    track_list_ptr = get_tracks()
    try:
        return _enumerate_timed_text_track_list_on_com_thread(self, track_list_ptr)
    finally:
        safe_release(track_list_ptr, 'timed text track list')



def _get_active_text_track_ids_on_com_thread(self: CoreState) -> Tuple[int, ...]:
    """Return active timed-text track ids directly from the COM thread.

    Edge cases:
        1. Some runtimes only expose the full track list, so active-state fallback must remain deterministic.
        2. Active-track list acquisition can fail independently from text-track enumeration and must degrade cleanly.
        3. Returned ids must be normalized to plain ints for stable UI/controller logic.
    """
    with self._state_lock:
        if self._shutdown_requested or not self._media_engine:
            return ()
    get_active_tracks = getattr(self, '_get_active_timed_text_tracks_on_com_thread', None)
    if callable(get_active_tracks):
        track_list_ptr = get_active_tracks()
        try:
            descriptors = _enumerate_timed_text_track_list_on_com_thread(self, track_list_ptr)
        finally:
            safe_release(track_list_ptr, 'active timed text track list')
    else:
        descriptors = _get_text_track_descriptors_on_com_thread(self)
    return tuple(int(d.get('track_id', 0)) for d in descriptors if d.get('is_active') and int(d.get('track_id', 0)) >= 0)



def get_text_track_descriptors(self: CoreState) -> Tuple[TrackDescriptor, ...]:
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return ()

    def _get() -> Tuple[TrackDescriptor, ...]:
        return _get_text_track_descriptors_on_com_thread(self)

    try:
        descriptors = adapter_obj.call_on_com_thread('get_text_track_descriptors', _get)
    except TimedTextMemoryError:
        raise
    except QUERY_EXCEPTIONS:
        logger.debug('[MediaEngineCore] timed-text query unavailable or failed.', exc_info=True)
        return ()
    return tuple(descriptor for descriptor in descriptors if isinstance(descriptor, dict))



def get_active_text_track_ids(self: CoreState) -> Tuple[int, ...]:
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return ()

    def _get() -> Tuple[int, ...]:
        return _get_active_text_track_ids_on_com_thread(self)

    try:
        active_ids = adapter_obj.call_on_com_thread('get_active_text_track_ids', _get)
    except TimedTextMemoryError:
        raise
    except QUERY_EXCEPTIONS:
        logger.debug('[MediaEngineCore] timed-text query unavailable or failed.', exc_info=True)
        return ()
    return tuple(int(track_id) for track_id in active_ids)



def _select_timed_text_track_on_com_thread(self: CoreState, track_id: int, selected: bool) -> None:
    """Dispatch a checked ID/boolean without implicit conversion or integer wraparound."""
    if type(track_id) is not int or not 0 <= track_id <= 0xFFFFFFFF or type(selected) is not bool:
        raise TimedTextBackendError('Selection requires a uint32 integer ID and a boolean.')
    call_timed_text = getattr(self, '_call_timed_text_method_on_com_thread', None)
    if not callable(call_timed_text):
        raise MediaEngineError('Timed-text support not available on the current backend.')
    hr = int(call_timed_text('SelectTrack', wintypes.DWORD(track_id), wintypes.BOOL(selected)))
    if not is_success(hr):
        raise MediaEngineError(f'IMFTimedText::SelectTrack failed hr={_hr_to_hex(hr)}')


def _check_engine_identity(self: CoreState, expected: EngineSnapshot) -> None:
    if _require_context(self) != expected:
        raise TimedTextBackendError('Engine changed during the timed-text selection sequence.')


def _apply_text_selection(self: CoreState, target: int | None) -> bool:
    """Prevalidate before mutation and report success only after active-ID readback.

    Selecting an unknown ID must not deselect valid tracks. Failed enumeration
    prevents every mutation. A failed later call can leave a partial selection;
    no transaction/rollback is claimed and the public method reports failure.
    """
    snapshot = _require_context(self)
    descriptors = _get_text_track_descriptors_on_com_thread(self)
    known_ids = {descriptor['track_id'] for descriptor in descriptors}
    _check_engine_identity(self, snapshot)
    if target is not None and target not in known_ids:
        return False
    if target is not None:
        _select_timed_text_track_on_com_thread(self, target, True)
    for descriptor in descriptors:
        _check_engine_identity(self, snapshot)
        if descriptor['track_id'] != target:
            _select_timed_text_track_on_com_thread(self, descriptor['track_id'], False)
    _check_engine_identity(self, snapshot)
    active_ids = set(_get_active_text_track_ids_on_com_thread(self))
    _check_engine_identity(self, snapshot)
    expected = set() if target is None else {target}
    if active_ids.intersection(known_ids) != expected:
        raise TimedTextBackendError('Timed-text active-ID readback does not confirm selection.')
    return True


def select_text_track(self: CoreState, track_id: int) -> bool:
    """Select an existing text track; refuse invalid IDs before entering COM."""
    if type(track_id) is not int or not 0 <= track_id <= 0xFFFFFFFF:
        return False
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return False
    try:
        return bool(adapter_obj.call_on_com_thread(
            'select_text_track', lambda: _apply_text_selection(self, track_id)))
    except TimedTextMemoryError:
        raise
    except QUERY_EXCEPTIONS:
        logger.debug('[MediaEngineCore] select_text_track(%s) failed.', track_id, exc_info=True)
        return False


def disable_text_tracks(self: CoreState) -> bool:
    """Disable available text tracks; unavailable service is not an empty success."""
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return False
    try:
        return bool(adapter_obj.call_on_com_thread(
            'disable_text_tracks', lambda: _apply_text_selection(self, None)))
    except TimedTextMemoryError:
        raise
    except QUERY_EXCEPTIONS:
        logger.debug('[MediaEngineCore] disable_text_tracks() failed.', exc_info=True)
        return False
