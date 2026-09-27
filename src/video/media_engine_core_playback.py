from __future__ import annotations

from .media_engine_seek import invalidate_seek, submit_seek
from .media_engine_core_stream_api import _ensure_media_engine_ex_on_com_thread

import ctypes
import logging
from ctypes import byref, c_double, c_void_p, wintypes
from typing import Tuple

from src.video.component_base.com_helpers import _hr_to_hex
from src.video.component_base.definitions import _SysAllocString, _SysFreeString
from src.video.component_base.utils import is_success

from .media_engine_core_shared import (
    MediaEngineError, CORE_PLAYBACK_EXCEPTIONS, CONVERSION_EXCEPTIONS, QUERY_EXCEPTIONS,
)

# Compatibility exports: existing callers keep the playback-module surface.
from .media_engine_core_timed_text import (
    _decode_timed_text_wstr,
    _enumerate_timed_text_track_list_on_com_thread,
    _get_active_text_track_ids_on_com_thread,
    _get_text_track_descriptors_on_com_thread,
    _read_timed_text_track_descriptor_on_com_thread,
    _select_timed_text_track_on_com_thread,
    disable_text_tracks,
    get_active_text_track_ids,
    get_text_track_descriptors,
    select_text_track,
)
from .media_engine_core_audio_streams import (
    _apply_audio_stream_selection_on_com_thread,
    _is_nonfatal_stream_query_error,
    _normalize_candidate_stream_indices,
    _selected_candidate_streams_on_com_thread,
    get_number_of_streams,
    get_selected_audio_streams,
    prime_audio_stream_candidates,
    select_audio_stream,
)

logger = logging.getLogger(__name__)

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

def load_source(self, source: str | None) -> None:
    """Select one URL/file source, or detach the current source with ``None``.

    Edge cases: repeated switches issue one SetSource each; shutdown/missing engine
    fail before dispatch; SetSource failure does not publish a new active source.
    """
    load_epoch = invalidate_seek(self, 'Source reload invalidated prior seek intent')
    with self._state_lock:
        if self._shutdown_requested:
            raise MediaEngineError('Shutdown gia richiesto: impossibile load_source().')
        if not self._media_engine:
            raise MediaEngineError(
                'MediaEngine non inizializzato: chiamare ensure_engine(hwnd) prima di load_source().'
            )
        local_source = None if source is None else str(source)
        if local_source is not None:
            self._source = local_source
            self._requested_source = local_source

    adapter_obj = self._adapter_ref()
    if not adapter_obj:
        raise MediaEngineError('Adapter non disponibile: impossibile completare load_source().')

    def _load_on_com_thread(src: str | None) -> None:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                raise MediaEngineError('MediaEngine non disponibile durante load_source() sul thread COM.')
            generation = self._engine_generation
        if src is None:
            hr = int(self._call_vtable_method('SetSource', c_void_p()))
            if hr != 0:
                raise MediaEngineError(f'SetSource(NULL) fallito hr={_hr_to_hex(hr)}')
        else:
            bstr = _SysAllocString(src)
            try:
                hr = int(self._call_vtable_method('SetSource', bstr))
                if hr != 0:
                    raise MediaEngineError(f'SetSource fallito hr={_hr_to_hex(hr)}')
            finally:
                _SysFreeString(bstr)
        with self._state_lock:
            if self._shutdown_requested or self._engine_generation != generation:
                raise MediaEngineError('Source owner changed during SetSource.')
            self._active_source = src
        if src is not None and not self._seek_slot.commit_source(load_epoch, generation, src):
            raise MediaEngineError('Source epoch changed during SetSource.')

    adapter_obj.call_on_com_thread('load', lambda: _load_on_com_thread(local_source))
    if local_source is None:
        logger.info('[MediaEngineCore] Native source detached with SetSource(NULL).')
    else:
        logger.info('[MediaEngineCore] Sorgente caricata con successo: %s', local_source)

def pause_for_source_switch(self) -> None:
    """Synchronously admit Pause before replacing an actively playing source.

    Edge cases: shutdown or a missing engine fails closed; Pause HRESULT failure is
    surfaced; completion remains asynchronous and is confirmed by the PAUSE event.
    """
    adapter_obj = self._adapter_ref()
    if not adapter_obj:
        raise MediaEngineError('Adapter non disponibile durante source-switch pause.')

    def _pause_on_com_thread() -> None:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                raise MediaEngineError('MediaEngine non disponibile durante source-switch pause.')
        hr = int(self._call_vtable_method('Pause'))
        if not is_success(hr):
            raise MediaEngineError(f'Pause pre-SetSource fallito hr={_hr_to_hex(hr)}')

    adapter_obj.call_on_com_thread('pause_source_switch', _pause_on_com_thread)

def stop(self) -> None:
    invalidate_seek(self, 'Stop invalidated prior seek intent')
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

def update_video_stream(self, width: int, height: int) -> bool:
    if getattr(self, '_wic_pipeline', None) is not None:
        return not self._shutdown_requested  # wx owns WIC geometry and coalesces resize.
    adapter_obj = self._adapter_ref()
    if not adapter_obj:
        return False

    width_i = max(0, int(width))
    height_i = max(0, int(height))
    if width_i <= 0 or height_i <= 0:
        return False


    def _update() -> bool:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return False
        engine_ex = _ensure_media_engine_ex_on_com_thread(self)

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

def seek(self, seconds: float) -> bool:
    """Return exact admission; the cached receipt records later native-call results."""
    return submit_seek(self, seconds)

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
    ("load_source", load_source), ("pause_for_source_switch", pause_for_source_switch),
    ("stop", stop), ("update_video_stream", update_video_stream),
    ("play", play), ("pause", pause), ("seek", seek),
    ("set_loop", set_loop), ("set_volume", set_volume), ("set_muted", set_muted),
    ("get_position", get_position), ("get_duration", get_duration),
    ("get_text_track_descriptors", get_text_track_descriptors),
    ("get_active_text_track_ids", get_active_text_track_ids),
    ("select_text_track", select_text_track), ("disable_text_tracks", disable_text_tracks),
    ("get_selected_audio_streams", get_selected_audio_streams),
    ("get_number_of_streams", get_number_of_streams),
    ("select_audio_stream", select_audio_stream),
    ("prime_audio_stream_candidates", prime_audio_stream_candidates),
    ("is_ended", is_ended), ("has_video", has_video), ("get_video_size", get_video_size),
)

def install_media_engine_core_playback_behavior(cls) -> None:
    """Install central playback methods exactly once; reloads remain idempotent."""
    if getattr(cls, "_media_engine_core_playback_behavior_attached", False):
        return
    for name, method in _MEDIA_ENGINE_CORE_PLAYBACK_METHODS:
        setattr(cls, name, method)
    setattr(cls, "_media_engine_core_playback_behavior_attached", True)

def attach_media_engine_core_playback_behavior(cls) -> None:
    """Compatibility shim for historical attach_* imports."""
    install_media_engine_core_playback_behavior(cls)
