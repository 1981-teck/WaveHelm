from __future__ import annotations

import ctypes
import logging
from ctypes import byref, c_double, c_void_p, wintypes
from typing import Tuple

from src.video.component_base.com_helpers import _hr_to_hex
from src.video.component_base.definitions import _SysAllocString, _SysFreeString
from src.video.component_base.utils import is_success, safe_release

from .media_engine_core_shared import MediaEngineError

logger = logging.getLogger(__name__)

CORE_PLAYBACK_EXCEPTIONS = (AttributeError, OSError, ReferenceError, RuntimeError, TypeError, ValueError)
CONVERSION_EXCEPTIONS = (TypeError, ValueError, OverflowError)
QUERY_EXCEPTIONS = CORE_PLAYBACK_EXCEPTIONS + CONVERSION_EXCEPTIONS
STREAM_QUERY_E_FAIL_MARKERS = (
    'IMFMediaEngineEx::GetNumberOfStreams failed: hr=0x80004005',
    'IMFMediaEngineEx::SetStreamSelection failed: hr=0x80004005',
    'IMFMediaEngineEx::ApplyStreamSelections failed: hr=0x80004005',
)


def _is_nonfatal_stream_query_error(exc: BaseException) -> bool:
    """Return whether a COM-side stream query failure should degrade cleanly.

    Edge cases:
        1. Media Foundation can return E_FAIL while the source is still warming up even though playback later succeeds.
        2. Stream-selection priming may be unsupported for some containers and must not abort the caller's default playback path.
        3. Unexpected exceptions must remain visible so real COM regressions do not get silently masked.
    """
    message = str(exc)
    return any(marker in message for marker in STREAM_QUERY_E_FAIL_MARKERS)


def _get_window_client_size(hwnd: int) -> Tuple[int, int] | None:
    """Best-effort client-size lookup from the real native host window.

    Edge cases:
        1. UI resize notifications can momentarily disagree with the real HWND client area during live drags.
        2. Win32 helpers are unavailable in non-Windows test environments and must degrade cleanly.
        3. Minimized or not-yet-laid-out windows can transiently report a zero-sized client rect.
    """
    hwnd_i = max(0, int(hwnd or 0))
    if hwnd_i <= 0:
        return None

    win_dll = getattr(ctypes, 'WinDLL', None)
    if win_dll is None:
        return None

    try:
        user32 = win_dll('user32', use_last_error=True)
        get_client_rect = user32.GetClientRect
        get_client_rect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        get_client_rect.restype = wintypes.BOOL
    except CORE_PLAYBACK_EXCEPTIONS:
        logger.debug('[MediaEngineCore] Win32 client-size lookup unavailable.', exc_info=True)
        return None

    rect = wintypes.RECT()
    try:
        if not bool(get_client_rect(wintypes.HWND(hwnd_i), byref(rect))):
            return None
    except CORE_PLAYBACK_EXCEPTIONS:
        logger.debug('[MediaEngineCore] GetClientRect failed for hwnd=%s.', hwnd_i, exc_info=True)
        return None

    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        return None
    return (width, height)


def _resolve_render_container_size(self, fallback_width: int, fallback_height: int) -> Tuple[int, int]:
    """Resolve the real destination size used by UpdateVideoStream.

    Edge cases:
        1. The backend host HWND can already have a different client size than the latest UI callback payload.
        2. The playback window handle can be missing while the engine is still bootstrapping, so we must fall back deterministically.
        3. A transient zero-sized native rect must not replace a valid positive fallback size.
    """
    playback_hwnd = max(0, int(getattr(self, '_playback_hwnd', 0) or 0))
    resolved = _get_window_client_size(playback_hwnd)
    if resolved is not None:
        return resolved
    return (fallback_width, fallback_height)


def load_source(self, source: str) -> None:
    with self._state_lock:
        if self._shutdown_requested:
            raise MediaEngineError('Shutdown gia richiesto: impossibile load_source().')
        if not self._media_engine:
            raise MediaEngineError(
                'MediaEngine non inizializzato: chiamare ensure_engine(hwnd) prima di load_source().'
            )
        local_source = str(source)
        self._source = local_source
        self._requested_source = local_source

    adapter_obj = self._adapter_ref()
    if not adapter_obj:
        raise MediaEngineError('Adapter non disponibile: impossibile completare load_source().')

    def _load_on_com_thread(src: str) -> None:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                raise MediaEngineError('MediaEngine non disponibile durante load_source() sul thread COM.')

        bstr = _SysAllocString(src)
        try:
            hr = int(self._call_vtable_method('SetSource', bstr))
            if not is_success(hr):
                raise MediaEngineError(f'SetSource fallito hr={_hr_to_hex(hr)}')

            hr = int(self._call_vtable_method('Load'))
            if not is_success(hr):
                raise MediaEngineError(f'Load fallito hr={_hr_to_hex(hr)}')

            with self._state_lock:
                self._active_source = src
        finally:
            _SysFreeString(bstr)

    adapter_obj.call_on_com_thread('load', lambda: _load_on_com_thread(local_source))
    logger.info('[MediaEngineCore] Sorgente caricata con successo: %s', local_source)


def stop(self) -> None:
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return

    def _stop() -> None:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return
            engine = self._media_engine
        self._stop_engine_ptr_playback(engine)

    try:
        adapter_obj.call_on_com_thread('stop', _stop)
    except CORE_PLAYBACK_EXCEPTIONS:
        logger.debug('[MediaEngineCore] stop() failed.', exc_info=True)


def update_video_stream(self, width: int, height: int) -> bool:
    adapter_obj = self._adapter_ref()
    if not adapter_obj:
        return False

    width_i = max(0, int(width))
    height_i = max(0, int(height))
    if width_i <= 0 or height_i <= 0:
        return False

    def _build_destination_rect(container_width: int, container_height: int) -> wintypes.RECT:
        """Return the full native client rect for Media Foundation rendering.

        Edge cases:
            1. The UI callback size can lag behind the actual native client rect during live resize drags.
            2. Width or height can transiently collapse to zero while the host window is minimized or not yet laid out.
            3. Manual letterboxing here would double-apply aspect-ratio correction and shrink the visible picture.
        """
        dst = wintypes.RECT()
        dst.left = 0
        dst.top = 0
        dst.right = max(0, int(container_width))
        dst.bottom = max(0, int(container_height))
        return dst

    def _update() -> bool:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return False
            engine_ex = self._media_engine_ex
            if engine_ex is None:
                engine_ex = self._query_media_engine_ex_on_com_thread(self._media_engine)
                self._media_engine_ex = engine_ex

        if engine_ex is None:
            return False

        container_width, container_height = _resolve_render_container_size(self, width_i, height_i)
        if container_width <= 0 or container_height <= 0:
            return False

        dst = _build_destination_rect(container_width, container_height)
        hr = int(
            self._call_engine_ptr_method(
                engine_ex,
                'UpdateVideoStream',
                c_void_p(),
                byref(dst),
                c_void_p(),
            )
        )
        if not is_success(hr):
            logger.debug(
                '[MediaEngineCore] UpdateVideoStream failed hr=%s callback_size=%sx%s render_size=%sx%s dst=(%s,%s,%s,%s)',
                _hr_to_hex(hr),
                width_i,
                height_i,
                container_width,
                container_height,
                int(dst.left),
                int(dst.top),
                int(dst.right),
                int(dst.bottom),
            )
            return False
        return True

    try:
        return bool(adapter_obj.call_on_com_thread('update_video_stream', _update))
    except CORE_PLAYBACK_EXCEPTIONS:
        logger.debug('[MediaEngineCore] update_video_stream() failed.', exc_info=True)
        return False


def play(self) -> None:
    adapter_obj = self._adapter_ref()
    if adapter_obj:
        adapter_obj.post_to_com_thread(
            'play',
            lambda: self._call_vtable_method('Play') if self._media_engine else None,
        )


def pause(self) -> None:
    adapter_obj = self._adapter_ref()
    if adapter_obj:
        adapter_obj.post_to_com_thread(
            'pause',
            lambda: self._call_vtable_method('Pause') if self._media_engine else None,
        )


def seek(self, seconds: float) -> None:
    adapter_obj = self._adapter_ref()
    if adapter_obj:
        adapter_obj.post_to_com_thread(
            'seek',
            lambda: self._call_vtable_method('SetCurrentTime', c_double(float(seconds))) if self._media_engine else None,
        )


def set_loop(self, enabled: bool) -> None:
    adapter_obj = self._adapter_ref()
    if adapter_obj:
        adapter_obj.post_to_com_thread(
            'set_loop',
            lambda: self._call_vtable_method('SetLoop', wintypes.BOOL(1 if enabled else 0)) if self._media_engine else None,
        )


def set_volume(self, volume: float) -> None:
    adapter_obj = self._adapter_ref()
    if adapter_obj:
        adapter_obj.post_to_com_thread(
            'set_volume',
            lambda: self._call_vtable_method('SetVolume', c_double(float(volume))) if self._media_engine else None,
        )


def set_muted(self, muted: bool) -> None:
    adapter_obj = self._adapter_ref()
    if adapter_obj:
        adapter_obj.post_to_com_thread(
            'set_muted',
            lambda: self._call_vtable_method('SetMuted', wintypes.BOOL(1 if muted else 0)) if self._media_engine else None,
        )


def get_position(self) -> float:
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return 0.0

    def _get() -> float:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return 0.0
        try:
            return self._sanitize_nonneg_float(self._call_vtable_method('GetCurrentTime'), default=0.0)
        except CORE_PLAYBACK_EXCEPTIONS:
            return 0.0

    try:
        return float(adapter_obj.call_on_com_thread('get_position', _get))
    except QUERY_EXCEPTIONS:
        return 0.0



def _decode_timed_text_wstr(raw_value) -> str:
    """Return a stable Python string from a Media Foundation timed-text wide-string out param.

    Edge cases:
        1. Timed-text getters can legally return a null string pointer for missing language or label metadata.
        2. Some bindings surface ctypes wrappers instead of plain Python strings, so coercion must stay defensive.
        3. The returned allocation can be backend-owned and cleanup must never break descriptor enumeration.
    """
    if raw_value is None:
        return ''
    try:
        value = getattr(raw_value, 'value', raw_value)
        if value is None:
            return ''
        return str(value).strip()
    except CORE_PLAYBACK_EXCEPTIONS:
        return ''
    finally:
        try:
            if raw_value:
                _SysFreeString(raw_value)
        except CORE_PLAYBACK_EXCEPTIONS:
            pass


def _read_timed_text_track_descriptor_on_com_thread(self, track_ptr) -> dict[str, object]:
    """Read one timed-text track descriptor from the COM-thread track pointer.

    Edge cases:
        1. Track getters can independently fail, so each field must degrade to a deterministic default.
        2. Missing language/label metadata must still produce a stable human-readable fallback label.
        3. COM pointers returned by track-list enumeration must always be released even when descriptor reads fail.
    """
    if not track_ptr:
        return {}

    vtbl = track_ptr.contents.lpVtbl.contents
    track_id = 0
    kind_value = 0
    is_active = False
    is_in_band = False
    language = ''
    label = ''

    try:
        track_id = int(vtbl.GetId(track_ptr))
    except CORE_PLAYBACK_EXCEPTIONS:
        track_id = 0

    try:
        kind_value = int(vtbl.GetTrackKind(track_ptr))
    except CORE_PLAYBACK_EXCEPTIONS:
        kind_value = 0

    try:
        is_active = bool(int(vtbl.IsActive(track_ptr)))
    except CORE_PLAYBACK_EXCEPTIONS:
        is_active = False

    try:
        is_in_band = bool(int(vtbl.IsInBand(track_ptr)))
    except CORE_PLAYBACK_EXCEPTIONS:
        is_in_band = False

    try:
        language_ptr = wintypes.LPWSTR()
        hr = int(vtbl.GetLanguage(track_ptr, byref(language_ptr)))
        if is_success(hr):
            language = _decode_timed_text_wstr(language_ptr)
    except CORE_PLAYBACK_EXCEPTIONS:
        language = ''

    try:
        label_ptr = wintypes.LPWSTR()
        hr = int(vtbl.GetLabel(track_ptr, byref(label_ptr)))
        if is_success(hr):
            label = _decode_timed_text_wstr(label_ptr)
    except CORE_PLAYBACK_EXCEPTIONS:
        label = ''

    kind_label = 'unknown' if kind_value == 0 else f'kind-{kind_value}'
    display_label = label or language or f'Subtitle {track_id}'
    return {
        'track_id': int(track_id),
        'kind': int(kind_value),
        'kind_label': kind_label,
        'language': language or '',
        'label': display_label,
        'raw_label': label or '',
        'is_active': bool(is_active),
        'is_in_band': bool(is_in_band),
    }


def _enumerate_timed_text_track_list_on_com_thread(self, track_list_ptr) -> Tuple[dict[str, object], ...]:
    """Enumerate a timed-text track list into stable Python descriptors.

    Edge cases:
        1. Empty or null track lists must produce an empty tuple without raising.
        2. Individual track acquisition can fail for one index and should not poison the entire enumeration.
        3. Every track pointer must be released after descriptor extraction to avoid leaking COM references.
    """
    if not track_list_ptr:
        return ()

    descriptors: list[dict[str, object]] = []
    vtbl = track_list_ptr.contents.lpVtbl.contents
    try:
        track_count = max(0, int(vtbl.GetLength(track_list_ptr)))
    except CORE_PLAYBACK_EXCEPTIONS:
        return ()

    for index in range(track_count):
        track_ref = None
        try:
            from src.video.component_base.definitions import IMFTimedTextTrack
            track_ref = ctypes.POINTER(IMFTimedTextTrack)()
            hr = int(vtbl.GetTrack(track_list_ptr, wintypes.DWORD(index), byref(track_ref)))
            if not is_success(hr) or not track_ref:
                continue
            descriptor = _read_timed_text_track_descriptor_on_com_thread(self, track_ref)
            if descriptor:
                descriptors.append(descriptor)
        except CORE_PLAYBACK_EXCEPTIONS:
            continue
        finally:
            safe_release(track_ref, f'timed text track {index}')

    return tuple(descriptors)


def _get_text_track_descriptors_on_com_thread(self) -> Tuple[dict[str, object], ...]:
    """Return timed-text descriptors directly from the COM thread without nested adapter dispatch.

    Edge cases:
        1. Backends can expose no timed-text getter at all and must degrade to an empty tuple.
        2. Getter failures must not leak partially enumerated COM pointers.
        3. Teardown can null the engine while callers are already on the COM thread, so state must be revalidated.
    """
    with self._state_lock:
        if self._shutdown_requested or not self._media_engine:
            return ()
    get_tracks = getattr(self, '_get_text_tracks_on_com_thread', None)
    if not callable(get_tracks):
        return ()
    track_list_ptr = get_tracks()
    try:
        return _enumerate_timed_text_track_list_on_com_thread(self, track_list_ptr)
    finally:
        safe_release(track_list_ptr, 'timed text track list')


def _get_active_text_track_ids_on_com_thread(self) -> Tuple[int, ...]:
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


def get_text_track_descriptors(self) -> Tuple[dict[str, object], ...]:
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return ()

    def _get() -> Tuple[dict[str, object], ...]:
        return _get_text_track_descriptors_on_com_thread(self)

    try:
        descriptors = adapter_obj.call_on_com_thread('get_text_track_descriptors', _get)
    except QUERY_EXCEPTIONS:
        return ()
    return tuple(descriptor for descriptor in descriptors if isinstance(descriptor, dict))


def get_active_text_track_ids(self) -> Tuple[int, ...]:
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return ()

    def _get() -> Tuple[int, ...]:
        return _get_active_text_track_ids_on_com_thread(self)

    try:
        active_ids = adapter_obj.call_on_com_thread('get_active_text_track_ids', _get)
    except QUERY_EXCEPTIONS:
        return ()
    return tuple(int(track_id) for track_id in active_ids)


def _select_timed_text_track_on_com_thread(self, track_id: int, selected: bool) -> None:
    """Apply one timed-text track selection change on the COM thread.

    Edge cases:
        1. Backends without timed-text support must fail explicitly instead of pretending selection succeeded.
        2. Track ids can arrive as non-int UI values and must be normalized before the COM boundary.
        3. HRESULT failures must surface with exact context so higher layers can report deterministic status.
    """
    call_timed_text = getattr(self, '_call_timed_text_method_on_com_thread', None)
    if not callable(call_timed_text):
        raise MediaEngineError('Timed-text support not available on the current backend.')
    normalized_track_id = self._normalize_stream_index(track_id) if hasattr(self, '_normalize_stream_index') else wintypes.DWORD(int(track_id))
    hr = int(call_timed_text('SelectTrack', normalized_track_id, wintypes.BOOL(1 if selected else 0)))
    if not is_success(hr):
        raise MediaEngineError(f'IMFTimedText::SelectTrack failed hr={_hr_to_hex(hr)}')


def select_text_track(self, track_id: int) -> bool:
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return False

    target_track_id = int(track_id)

    def _select() -> bool:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return False
        descriptors = _get_text_track_descriptors_on_com_thread(self)
        known_track_ids = [int(d.get('track_id', -1)) for d in descriptors if int(d.get('track_id', -1)) >= 0]
        for known_track_id in known_track_ids:
            _select_timed_text_track_on_com_thread(self, known_track_id, known_track_id == target_track_id)
        if target_track_id not in known_track_ids:
            _select_timed_text_track_on_com_thread(self, target_track_id, True)
        return True

    try:
        return bool(adapter_obj.call_on_com_thread('select_text_track', _select))
    except QUERY_EXCEPTIONS:
        logger.debug('[MediaEngineCore] select_text_track(%s) failed.', target_track_id, exc_info=True)
        return False


def disable_text_tracks(self) -> bool:
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return False

    def _disable() -> bool:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return False
        descriptors = _get_text_track_descriptors_on_com_thread(self)
        for descriptor in descriptors:
            track_id = int(descriptor.get('track_id', -1))
            if track_id >= 0:
                _select_timed_text_track_on_com_thread(self, track_id, False)
        return True

    try:
        return bool(adapter_obj.call_on_com_thread('disable_text_tracks', _disable))
    except QUERY_EXCEPTIONS:
        logger.debug('[MediaEngineCore] disable_text_tracks() failed.', exc_info=True)
        return False

def get_duration(self) -> float:
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return 0.0

    def _get() -> float:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return 0.0
        try:
            return self._sanitize_nonneg_float(self._call_vtable_method('GetDuration'), default=0.0)
        except CORE_PLAYBACK_EXCEPTIONS:
            return 0.0

    try:
        return float(adapter_obj.call_on_com_thread('get_duration', _get))
    except QUERY_EXCEPTIONS:
        return 0.0


def _normalize_candidate_stream_indices(candidate_stream_indices) -> Tuple[int, ...]:
    """Return a stable, de-duplicated tuple of stream indices for audio selection work.

    Edge cases:
        1. Callers can pass None or an empty iterable and must get a deterministic empty tuple.
        2. Mixed numeric/string values must coerce to plain ints without leaking duplicates.
        3. Negative or non-numeric entries must fail early before any COM-side stream mutation.
    """
    if candidate_stream_indices is None:
        return ()

    normalized: list[int] = []
    seen: set[int] = set()
    for raw_index in tuple(candidate_stream_indices):
        try:
            stream_index = int(raw_index)
        except CONVERSION_EXCEPTIONS as exc:
            raise ValueError(f'Invalid audio stream index: {raw_index!r}') from exc
        if stream_index < 0:
            raise ValueError(f'Invalid audio stream index: {raw_index!r}')
        if stream_index in seen:
            continue
        seen.add(stream_index)
        normalized.append(stream_index)
    return tuple(normalized)


def _selected_candidate_streams_on_com_thread(self, candidate_stream_indices: Tuple[int, ...]) -> Tuple[int, ...]:
    """Return the currently selected stream indices among a bounded candidate set.

    Edge cases:
        1. Empty candidate sets must return an empty tuple without touching Media Foundation state.
        2. Stream-selection probes can fail per-index, so errors must retain exact context for callers.
        3. Duplicate candidate indices must already be normalized so the result ordering stays stable.
    """
    if not candidate_stream_indices:
        return ()

    selected_indices: list[int] = []
    for stream_index in candidate_stream_indices:
        if self._get_stream_selection_on_com_thread(stream_index):
            selected_indices.append(stream_index)
    return tuple(selected_indices)


def get_selected_audio_streams(self, candidate_stream_indices) -> Tuple[int, ...]:
    adapter_obj = self._adapter_ref()
    normalized_candidates = _normalize_candidate_stream_indices(candidate_stream_indices)
    if not adapter_obj or not self._media_engine or not normalized_candidates:
        return ()

    def _get() -> Tuple[int, ...]:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return ()
        return _selected_candidate_streams_on_com_thread(self, normalized_candidates)

    try:
        selected = adapter_obj.call_on_com_thread('get_selected_audio_streams', _get)
    except QUERY_EXCEPTIONS:
        return ()
    if not isinstance(selected, tuple):
        return ()
    return tuple(int(stream_index) for stream_index in selected)


def get_number_of_streams(self) -> int:
    """Return the Media Foundation stream count for the currently loaded source.

    Edge cases:
        1. Legacy or partially initialized adapters can lack a live engine and must degrade to 0 without raising.
        2. COM-side stream queries can fail transiently right after source load and must not abort controller-level fallback planning.
        3. Returned values must normalize to a bounded non-negative int so runtime candidate bootstrapping stays deterministic.
    """
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return 0

    def _get() -> int:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return 0
        try:
            return self._get_number_of_streams_on_com_thread()
        except RuntimeError as exc:
            if not _is_nonfatal_stream_query_error(exc):
                raise
            logger.debug('[MediaEngineCore] Non-fatal GetNumberOfStreams failure during source warm-up.', exc_info=True)
            return 0

    try:
        stream_count = adapter_obj.call_on_com_thread('get_number_of_streams', _get)
    except QUERY_EXCEPTIONS:
        return 0
    try:
        return max(0, int(stream_count or 0))
    except CONVERSION_EXCEPTIONS:
        return 0


def _apply_audio_stream_selection_on_com_thread(
    self,
    target_stream_index: int,
    candidate_stream_indices: Tuple[int, ...],
) -> int:
    """Apply one deterministic audio-stream selection across the provided candidate set.

    Edge cases:
        1. The target stream can be missing from the candidate list and must still be selected explicitly.
        2. Previously selected fallback streams must be deselected first to avoid ambiguous multi-audio state.
        3. ApplyStreamSelections can fail after per-stream changes, so callers need the exact failing context.
    """
    ordered_candidates = candidate_stream_indices
    if target_stream_index not in ordered_candidates:
        ordered_candidates = ordered_candidates + (target_stream_index,)

    for stream_index in ordered_candidates:
        self._set_stream_selection_on_com_thread(stream_index, stream_index == target_stream_index)
    self._apply_stream_selections_on_com_thread()
    return int(target_stream_index)


def select_audio_stream(self, stream_index: int, candidate_stream_indices=None) -> bool:
    adapter_obj = self._adapter_ref()
    target_stream_index = int(stream_index)
    normalized_candidates = _normalize_candidate_stream_indices(candidate_stream_indices)
    if not adapter_obj or not self._media_engine:
        return False

    def _select() -> bool:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return False
        _apply_audio_stream_selection_on_com_thread(self, target_stream_index, normalized_candidates)
        return True

    try:
        return bool(adapter_obj.call_on_com_thread('select_audio_stream', _select))
    except QUERY_EXCEPTIONS:
        logger.debug('[MediaEngineCore] select_audio_stream(%s) failed.', target_stream_index, exc_info=True)
        return False


def prime_audio_stream_candidates(self, candidate_stream_indices) -> int | None:
    adapter_obj = self._adapter_ref()
    normalized_candidates = _normalize_candidate_stream_indices(candidate_stream_indices)
    if not adapter_obj or not self._media_engine or not normalized_candidates:
        return None

    def _prime() -> int | None:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return None
        try:
            return _apply_audio_stream_selection_on_com_thread(self, normalized_candidates[0], normalized_candidates)
        except RuntimeError as exc:
            if not _is_nonfatal_stream_query_error(exc):
                raise
            logger.debug('[MediaEngineCore] Non-fatal audio-stream priming failure; keeping default engine selection.', exc_info=True)
            return None

    try:
        selected = adapter_obj.call_on_com_thread('prime_audio_stream_candidates', _prime)
    except QUERY_EXCEPTIONS:
        logger.debug('[MediaEngineCore] prime_audio_stream_candidates() failed.', exc_info=True)
        return None
    if selected is None:
        return None
    try:
        return int(selected)
    except CONVERSION_EXCEPTIONS:
        return None


def is_ended(self) -> bool:
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return False

    def _get() -> bool:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return False
        try:
            ended_val = self._call_vtable_method('IsEnded')
        except CORE_PLAYBACK_EXCEPTIONS:
            ended_val = self._call_vtable_method('GetEnded')
        try:
            return bool(int(ended_val))
        except CONVERSION_EXCEPTIONS:
            return False

    try:
        return bool(adapter_obj.call_on_com_thread('is_ended', _get))
    except CORE_PLAYBACK_EXCEPTIONS:
        return False


def has_video(self) -> bool:
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return False

    def _get() -> bool:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return False
        try:
            v = self._call_vtable_method('HasVideo')
            return bool(int(v))
        except QUERY_EXCEPTIONS:
            return False

    try:
        return bool(adapter_obj.call_on_com_thread('has_video', _get))
    except CORE_PLAYBACK_EXCEPTIONS:
        return False


def get_video_size(self) -> Tuple[int, int]:
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return (0, 0)

    def _get() -> Tuple[int, int]:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return (0, 0)

        cx, cy = wintypes.DWORD(0), wintypes.DWORD(0)
        try:
            hr = int(self._call_vtable_method('GetNativeVideoSize', byref(cx), byref(cy)))
            if is_success(hr):
                return (int(cx.value), int(cy.value))
        except CORE_PLAYBACK_EXCEPTIONS:
            logger.debug('[MediaEngineCore] GetNativeVideoSize failed', exc_info=True)
        return (0, 0)

    try:
        res = adapter_obj.call_on_com_thread('get_video_size', _get)
        if isinstance(res, tuple) and len(res) == 2:
            return (int(res[0]), int(res[1]))
        return (0, 0)
    except QUERY_EXCEPTIONS:
        return (0, 0)


_MEDIA_ENGINE_CORE_PLAYBACK_METHODS = (
    ("load_source", load_source),
    ("stop", stop),
    ("update_video_stream", update_video_stream),
    ("play", play),
    ("pause", pause),
    ("seek", seek),
    ("set_loop", set_loop),
    ("set_volume", set_volume),
    ("set_muted", set_muted),
    ("get_position", get_position),
    ("get_duration", get_duration),
    ("get_text_track_descriptors", get_text_track_descriptors),
    ("get_active_text_track_ids", get_active_text_track_ids),
    ("select_text_track", select_text_track),
    ("disable_text_tracks", disable_text_tracks),
    ("get_selected_audio_streams", get_selected_audio_streams),
    ("get_number_of_streams", get_number_of_streams),
    ("select_audio_stream", select_audio_stream),
    ("prime_audio_stream_candidates", prime_audio_stream_candidates),
    ("is_ended", is_ended),
    ("has_video", has_video),
    ("get_video_size", get_video_size),
)


def install_media_engine_core_playback_behavior(cls) -> None:
    """Install MediaEngineCore playback behavior on the central class.

    Edge cases:
        - Re-running the installer during reloads can silently override playback patches.
        - Split-module imports can leave the coordinator with a partial playback surface.
        - Legacy leaf entrypoints can drift from the central playback contract over time.
    """
    if getattr(cls, "_media_engine_core_playback_behavior_attached", False):
        return

    for name, method in _MEDIA_ENGINE_CORE_PLAYBACK_METHODS:
        setattr(cls, name, method)

    setattr(cls, "_media_engine_core_playback_behavior_attached", True)


def attach_media_engine_core_playback_behavior(cls) -> None:
    """Compatibility shim for historical attach_* imports.

    Prefer install_media_engine_core_playback_behavior() from the central coordinator path.
    """
    install_media_engine_core_playback_behavior(cls)
