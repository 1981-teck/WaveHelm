from __future__ import annotations

from typing import Callable, Tuple

from .mf_base import logger

STATE_EXCEPTIONS = (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError)
CORE_EXCEPTIONS = (AttributeError, OSError, ReferenceError, RuntimeError, TypeError, ValueError)
COERCE_EXCEPTIONS = (TypeError, ValueError, OverflowError)


def _is_engine_initialized(self) -> bool:
    """Ritorna True se il core ha un MediaEngine COM valido (best effort)."""
    try:
        ptr = getattr(self._core, '_media_engine', None) or getattr(self._core, '_engine', None)
        return bool(ptr)
    except STATE_EXCEPTIONS:
        return False


def _can_issue_playback_commands(self) -> bool:
    """True se e sicuro inviare comandi playback (non in shutdown, engine pronto)."""
    try:
        with self._lock:
            if self._shutdown_requested or self._closed:
                return False
        return bool(self._is_engine_initialized())
    except STATE_EXCEPTIONS:
        return False


def _require_active_engine(self, operation: str) -> None:
    """Validate adapter preconditions before accepting playback commands."""
    with self._lock:
        if self._shutdown_requested or self._closed:
            raise RuntimeError(f'Cannot execute {operation}: adapter is shutting down or already closed.')

    if not self._is_engine_initialized():
        raise RuntimeError(f'Cannot execute {operation}: MediaEngine is not initialized.')


def startup(self) -> None:
    """Garantisce che il thread COM dedicato sia avviato (lazy) prima dell'uso."""
    logger.debug('[IMFAdapter] startup() called (ensure COM thread)')
    try:
        self._get_com_thread_manager(ensure_started=True)
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] startup failed', exc_info=True)


def shutdown(self) -> None:
    """Shutdown completo: rilascia engine (idempotente)."""
    with self._lock:
        if self._closed or self._shutdown_requested:
            return
        self._shutdown_requested = True
        logger.info('[IMFAdapter] shutdown()')

    try:
        self._core.shutdown()
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] core.shutdown failed (best effort)', exc_info=True)
    finally:
        with self._lock:
            self._closed = True


def close(self) -> None:
    """Compat: alias per shutdown()."""
    self.shutdown()


def stop(self) -> None:
    """Compat: stop playback."""
    if not self._can_issue_playback_commands():
        logger.debug('[IMFAdapter] stop ignored (shutdown or engine not initialized)')
        return

    self._core.stop()


def set_hwnd(self, hwnd: int) -> None:
    """Binda l'HWND di output all'engine."""
    if not isinstance(hwnd, int) or hwnd <= 0:
        raise ValueError(f'HWND non valido: {hwnd!r}')

    with self._lock:
        if self._shutdown_requested:
            raise RuntimeError('Shutdown gia richiesto: impossibile set_hwnd()')
        self._hwnd = hwnd

    logger.info('[IMFAdapter] set_hwnd(HWND=%s)', hwnd)

    try:
        self._core.ensure_engine(hwnd)
    except CORE_EXCEPTIONS:
        logger.error('[IMFAdapter] ensure_engine failed for HWND=%s', hwnd, exc_info=True)
        raise

    self._apply_settings_core()


def bind_hwnd(self, hwnd: int) -> None:
    return self.set_hwnd(hwnd)


def set_target_hwnd(self, hwnd: int) -> None:
    return self.set_hwnd(hwnd)


def set_video_window(self, hwnd: int) -> None:
    return self.set_hwnd(hwnd)


def set_loop(self, enabled: bool) -> None:
    with self._lock:
        self._loop_enabled = bool(enabled)

    if self._is_engine_initialized():
        self._core.set_loop(self._loop_enabled)


def set_loop_enabled(self, enabled: bool) -> None:
    self.set_loop(enabled)


def refresh_video_window(self, hwnd: int, width: int, height: int) -> bool:
    if not self._is_engine_initialized():
        return False

    with self._lock:
        if self._shutdown_requested or self._closed:
            return False
        if hwnd and int(hwnd) > 0:
            self._hwnd = int(hwnd)

    return bool(self._core.update_video_stream(int(width), int(height)))


def set_volume(self, volume: float) -> None:
    with self._lock:
        try:
            v = float(volume)
        except COERCE_EXCEPTIONS:
            v = 1.0
        self._volume = max(0.0, min(1.0, v))

    if self._can_issue_playback_commands():
        try:
            self._core.set_volume(self._volume)
        except CORE_EXCEPTIONS:
            logger.debug('[IMFAdapter] set_volume failed', exc_info=True)


def set_muted(self, muted: bool) -> None:
    with self._lock:
        self._muted = bool(muted)

    if self._can_issue_playback_commands():
        try:
            self._core.set_muted(self._muted)
        except CORE_EXCEPTIONS:
            logger.debug('[IMFAdapter] set_muted failed', exc_info=True)


def resume(self) -> None:
    self.play()


def load_source(self, path: str) -> None:
    if not path:
        raise ValueError('Video source path must not be empty.')

    with self._lock:
        if self._shutdown_requested or self._closed:
            raise RuntimeError('Cannot load video source while shutdown is in progress.')
        src = str(path)
        self._source = src

    self._require_active_engine('load_source')
    logger.info('[IMFAdapter] load_source(%s)', src)
    self._core.load_source(src)


def load_video(self, path: str, loop: bool = False) -> bool:
    try:
        self.set_loop_enabled(loop)
        self.load_source(path)
        self.play()
        return True
    except CORE_EXCEPTIONS:
        logger.error('[IMFAdapter] load_video failed', exc_info=True)
        return False


def get_selected_audio_streams(self, candidate_stream_indices) -> Tuple[int, ...]:
    """Return the selected audio streams among the provided candidate set.

    Edge cases:
        1. Shutdown or missing engine state must return an empty tuple without touching COM state.
        2. Core selection probes can fail on malformed candidate indices and must degrade deterministically.
        3. Legacy callers can pass mixed numeric values that still need stable int coercion on success.
    """
    if not self._is_engine_initialized():
        return ()

    with self._lock:
        if self._shutdown_requested or self._closed:
            return ()

    try:
        selected = self._core.get_selected_audio_streams(candidate_stream_indices)
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] get_selected_audio_streams failed', exc_info=True)
        return ()

    if not isinstance(selected, tuple):
        return ()

    normalized_selected: list[int] = []
    for raw_index in selected:
        try:
            normalized_selected.append(int(raw_index))
        except COERCE_EXCEPTIONS:
            logger.debug('[IMFAdapter] Ignoring invalid selected audio stream index: %r', raw_index)
    return tuple(normalized_selected)


def get_number_of_streams(self) -> int:
    """Return the current Media Foundation stream count through the adapter boundary.

    Edge cases:
        1. Shutdown or missing engine state must return 0 without touching COM stream state.
        2. Core-side count queries can fail transiently after load_source and must degrade deterministically.
        3. Legacy callers can use the result to bootstrap generic fallback candidates, so normalization must clamp to a plain non-negative int.
    """
    if not self._is_engine_initialized():
        return 0

    with self._lock:
        if self._shutdown_requested or self._closed:
            return 0

    try:
        stream_count = self._core.get_number_of_streams()
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] get_number_of_streams failed', exc_info=True)
        return 0

    try:
        return max(0, int(stream_count or 0))
    except COERCE_EXCEPTIONS:
        return 0


def select_audio_stream(self, stream_index: int, candidate_stream_indices=None) -> bool:
    """Select one audio stream deterministically through the adapter boundary.

    Edge cases:
        1. Selection before engine startup must fail fast instead of mutating adapter state silently.
        2. Invalid stream identifiers must be rejected before reaching the Media Foundation boundary.
        3. Core selection failures must surface as a clean False result for controller-level fallback logic.
    """
    self._require_active_engine('select_audio_stream')
    try:
        target_stream_index = int(stream_index)
    except COERCE_EXCEPTIONS as exc:
        raise ValueError(f'Invalid audio stream index: {stream_index!r}') from exc

    try:
        return bool(self._core.select_audio_stream(target_stream_index, candidate_stream_indices))
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] select_audio_stream(%s) failed', target_stream_index, exc_info=True)
        return False


def prime_audio_stream_candidates(self, candidate_stream_indices) -> int | None:
    """Prime the first viable audio-stream candidate before playback starts.

    Edge cases:
        1. Empty candidate lists must return None without attempting any Media Foundation mutation.
        2. Shutdown races must fail fast so startup does not continue with partially primed stream state.
        3. Core priming can return non-int values on legacy paths and must normalize or drop them safely.
    """
    self._require_active_engine('prime_audio_stream_candidates')
    try:
        selected = self._core.prime_audio_stream_candidates(candidate_stream_indices)
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] prime_audio_stream_candidates failed', exc_info=True)
        return None

    if selected is None:
        return None

    try:
        return int(selected)
    except COERCE_EXCEPTIONS:
        logger.debug('[IMFAdapter] Invalid primed audio stream index: %r', selected)
        return None



def get_text_track_descriptors(self) -> Tuple[dict[str, object], ...]:
    """Return sanitized timed-text track descriptors through the adapter boundary.

    Edge cases:
        1. Shutdown or missing engine state must return an empty tuple without touching timed-text COM state.
        2. Core enumeration can yield partial or malformed descriptor dictionaries that still need stable normalization.
        3. Legacy backends can expose non-int ids or non-bool flags and must be coerced or filtered deterministically.
    """
    if not self._is_engine_initialized():
        return ()

    with self._lock:
        if self._shutdown_requested or self._closed:
            return ()

    try:
        descriptors = self._core.get_text_track_descriptors()
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] get_text_track_descriptors failed', exc_info=True)
        return ()

    if not isinstance(descriptors, tuple):
        return ()

    normalized_descriptors: list[dict[str, object]] = []
    for raw_descriptor in descriptors:
        if not isinstance(raw_descriptor, dict):
            continue
        try:
            track_id = int(raw_descriptor.get('track_id', -1))
        except COERCE_EXCEPTIONS:
            logger.debug('[IMFAdapter] Ignoring invalid text track descriptor id: %r', raw_descriptor)
            continue
        if track_id < 0:
            continue

        normalized_descriptor = dict(raw_descriptor)
        normalized_descriptor['track_id'] = track_id
        normalized_descriptor['kind'] = int(raw_descriptor.get('kind', 0) or 0)
        normalized_descriptor['kind_label'] = str(raw_descriptor.get('kind_label') or '')
        normalized_descriptor['language'] = str(raw_descriptor.get('language') or '')
        normalized_descriptor['label'] = str(raw_descriptor.get('label') or '')
        normalized_descriptor['raw_label'] = str(raw_descriptor.get('raw_label') or '')
        normalized_descriptor['is_active'] = bool(raw_descriptor.get('is_active'))
        normalized_descriptor['is_in_band'] = bool(raw_descriptor.get('is_in_band'))
        normalized_descriptors.append(normalized_descriptor)
    return tuple(normalized_descriptors)


def get_active_text_track_ids(self) -> Tuple[int, ...]:
    """Return active timed-text track ids in a stable normalized tuple.

    Edge cases:
        1. Shutdown or missing engine state must degrade to an empty tuple without touching COM state.
        2. Core active-track probes can fail independently from track enumeration and must not bubble runtime errors.
        3. Legacy paths can return mixed numeric representations that still need plain-int normalization.
    """
    if not self._is_engine_initialized():
        return ()

    with self._lock:
        if self._shutdown_requested or self._closed:
            return ()

    try:
        active_track_ids = self._core.get_active_text_track_ids()
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] get_active_text_track_ids failed', exc_info=True)
        return ()

    if not isinstance(active_track_ids, tuple):
        return ()

    normalized_track_ids: list[int] = []
    for raw_track_id in active_track_ids:
        try:
            track_id = int(raw_track_id)
        except COERCE_EXCEPTIONS:
            logger.debug('[IMFAdapter] Ignoring invalid active text track id: %r', raw_track_id)
            continue
        if track_id >= 0:
            normalized_track_ids.append(track_id)
    return tuple(normalized_track_ids)


def select_text_track(self, track_id: int) -> bool:
    """Select one timed-text track deterministically through the adapter boundary.

    Edge cases:
        1. Selection before engine startup must fail fast instead of mutating adapter state silently.
        2. Invalid track identifiers must be rejected before reaching the Media Foundation boundary.
        3. Core selection failures must surface as a clean False result for controller-level fallback logic.
    """
    self._require_active_engine('select_text_track')
    try:
        target_track_id = int(track_id)
    except COERCE_EXCEPTIONS as exc:
        raise ValueError(f'Invalid text track id: {track_id!r}') from exc

    try:
        return bool(self._core.select_text_track(target_track_id))
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] select_text_track(%s) failed', target_track_id, exc_info=True)
        return False


def disable_text_tracks(self) -> bool:
    """Disable all timed-text tracks through the adapter boundary.

    Edge cases:
        1. Disabling before engine startup must fail fast instead of silently pretending subtitles were cleared.
        2. Shutdown races must not leave higher layers believing the timed-text state was reset when it was not.
        3. Core disable failures must degrade to False so UI/controller layers can report deterministic status.
    """
    self._require_active_engine('disable_text_tracks')
    try:
        return bool(self._core.disable_text_tracks())
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] disable_text_tracks failed', exc_info=True)
        return False

def play(self) -> None:
    self._require_active_engine('play')
    self._core.play()


def pause(self) -> None:
    self._require_active_engine('pause')
    self._core.pause()


def seek(self, seconds: float) -> None:
    self._require_active_engine('seek')
    self._core.seek(float(seconds))


def get_position(self) -> float:
    try:
        return float(self._core.get_position())
    except CORE_EXCEPTIONS:
        return 0.0


def get_duration(self) -> float:
    try:
        return float(self._core.get_duration())
    except CORE_EXCEPTIONS:
        return 0.0


def has_ended(self) -> bool:
    try:
        return bool(self._core.is_ended())
    except CORE_EXCEPTIONS:
        return False


def get_video_size(self) -> Tuple[int, int]:
    try:
        size = self._core.get_video_size()
        if isinstance(size, tuple) and len(size) == 2:
            return (int(size[0]), int(size[1]))
        return (0, 0)
    except CORE_EXCEPTIONS:
        return (0, 0)


def has_video(self) -> bool:
    try:
        return bool(self._core.has_video())
    except CORE_EXCEPTIONS:
        return False


def _apply_settings_core(self) -> None:
    """Applica impostazioni correnti su MediaEngineCore (best effort)."""
    if not self._is_engine_initialized():
        return

    with self._lock:
        loop_enabled = self._loop_enabled
        volume = self._volume
        muted = self._muted
        shutdown = self._shutdown_requested or self._closed

    if shutdown:
        return

    try:
        self._core.set_loop(loop_enabled)
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] apply set_loop failed', exc_info=True)

    try:
        if volume != 1.0:
            self._core.set_volume(volume)
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] apply set_volume failed', exc_info=True)

    try:
        if muted:
            self._core.set_muted(True)
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] apply set_muted failed', exc_info=True)


_IMF_MEDIA_ENGINE_ADAPTER_PLAYBACK_METHODS: tuple[tuple[str, Callable[..., object]], ...] = (
    ("_is_engine_initialized", _is_engine_initialized),
    ("_can_issue_playback_commands", _can_issue_playback_commands),
    ("_require_active_engine", _require_active_engine),
    ("startup", startup),
    ("shutdown", shutdown),
    ("close", close),
    ("stop", stop),
    ("set_hwnd", set_hwnd),
    ("bind_hwnd", bind_hwnd),
    ("set_target_hwnd", set_target_hwnd),
    ("set_video_window", set_video_window),
    ("set_loop", set_loop),
    ("set_loop_enabled", set_loop_enabled),
    ("refresh_video_window", refresh_video_window),
    ("set_volume", set_volume),
    ("set_muted", set_muted),
    ("resume", resume),
    ("load_source", load_source),
    ("load_video", load_video),
    ("get_selected_audio_streams", get_selected_audio_streams),
    ("get_number_of_streams", get_number_of_streams),
    ("select_audio_stream", select_audio_stream),
    ("prime_audio_stream_candidates", prime_audio_stream_candidates),
    ("get_text_track_descriptors", get_text_track_descriptors),
    ("get_active_text_track_ids", get_active_text_track_ids),
    ("select_text_track", select_text_track),
    ("disable_text_tracks", disable_text_tracks),
    ("play", play),
    ("pause", pause),
    ("seek", seek),
    ("get_position", get_position),
    ("get_duration", get_duration),
    ("has_ended", has_ended),
    ("get_video_size", get_video_size),
    ("has_video", has_video),
    ("_apply_settings_core", _apply_settings_core),
)


def install_imf_media_engine_adapter_playback_behavior(cls) -> None:
    """Install playback behavior on the adapter leaf module.

    Edge cases:
        - Duplicate installs during reloads can silently rebind methods.
        - Partial imports can leave playback helpers attached only in part.
        - Legacy callers can still import the old attach_* entrypoint directly.
    """
    if getattr(cls, "_imf_media_engine_adapter_playback_behavior_attached", False):
        return

    for name, method in _IMF_MEDIA_ENGINE_ADAPTER_PLAYBACK_METHODS:
        setattr(cls, name, method)

    setattr(cls, "_imf_media_engine_adapter_playback_behavior_attached", True)


def attach_imf_media_engine_adapter_playback_behavior(cls) -> None:
    """Backward-compatible shim for legacy attach_* imports."""
    install_imf_media_engine_adapter_playback_behavior(cls)
