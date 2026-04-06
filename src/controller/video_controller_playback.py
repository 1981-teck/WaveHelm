from __future__ import annotations

import logging
import time
from typing import Any, Callable

from src.audio.audio_events import AudioEventType
from .video_controller_state import VideoState

logger = logging.getLogger(__name__)

ADAPTER_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError, OSError)
FORMAT_EXCEPTIONS = (TypeError, ValueError)



def _normalize_audio_track_candidates(candidate_stream_indices: Any) -> tuple[int, ...]:
    """Return a stable tuple of candidate audio stream indices for controller-level fallback.

    Edge cases:
        1. Callers can pass None or malformed metadata and must get an empty tuple instead of a runtime error.
        2. Mixed numeric values may contain duplicates and need deterministic int coercion before adapter calls.
        3. Negative or non-numeric stream identifiers must be ignored so controller retries remain bounded and stable.
    """
    if candidate_stream_indices is None:
        return ()

    normalized: list[int] = []
    seen: set[int] = set()
    try:
        raw_candidates = tuple(candidate_stream_indices)
    except FORMAT_EXCEPTIONS:
        return ()

    for raw_index in raw_candidates:
        try:
            stream_index = int(raw_index)
        except FORMAT_EXCEPTIONS:
            continue
        if stream_index < 0 or stream_index in seen:
            continue
        seen.add(stream_index)
        normalized.append(stream_index)
    return tuple(normalized)



def get_audio_track_candidates(self) -> tuple[int, ...]:
    """Return the current controller-level audio track candidates.

    Edge cases:
        1. Older callers may not have initialized any audio-track metadata on the controller.
        2. Metadata payloads can be non-dicts or missing the candidate list entirely.
        3. Candidate lists can contain invalid items and must normalize to a deterministic bounded tuple.
    """
    direct_candidates = getattr(self, '_current_audio_track_candidates', None)
    if direct_candidates is not None:
        return _normalize_audio_track_candidates(direct_candidates)

    metadata = getattr(self, '_current_media_metadata', None)
    if isinstance(metadata, dict):
        return _normalize_audio_track_candidates(metadata.get('audio_track_candidates'))
    return ()



def set_audio_track_candidates(self, candidate_stream_indices: Any) -> tuple[int, ...]:
    """Store deterministic audio-track candidates on the controller for playback fallback and UI.

    Edge cases:
        1. Legacy callers can provide None and must clear the current candidates cleanly.
        2. Mixed numeric/string stream identifiers must normalize before any playback retry uses them.
        3. Invalid values must not leak to the adapter boundary or create unbounded retry loops.
    """
    normalized_candidates = _normalize_audio_track_candidates(candidate_stream_indices)
    self._current_audio_track_candidates = normalized_candidates
    if not normalized_candidates:
        self._current_audio_stream_index = None
    return normalized_candidates



def get_selected_audio_streams(self) -> tuple[int, ...]:
    """Return the currently selected audio streams among the controller candidates."""
    if not self._adapter:
        return ()

    candidates = self.get_audio_track_candidates()
    if not candidates:
        return ()

    adapter_getter = getattr(self._adapter, 'get_selected_audio_streams', None)
    if not callable(adapter_getter):
        return ()

    try:
        selected = adapter_getter(candidates)
    except ADAPTER_EXCEPTIONS:
        logger.debug('[VideoController] get_selected_audio_streams failed', exc_info=True)
        return ()
    return _normalize_audio_track_candidates(selected)



def select_audio_stream(self, stream_index: int) -> bool:
    """Select one audio stream through the controller boundary using the current candidate set."""
    if not self._adapter:
        return False

    adapter_selector = getattr(self._adapter, 'select_audio_stream', None)
    if not callable(adapter_selector):
        return False

    try:
        target_stream_index = int(stream_index)
    except FORMAT_EXCEPTIONS:
        logger.debug('[VideoController] select_audio_stream rejected invalid index: %r', stream_index)
        return False

    if target_stream_index < 0:
        return False

    candidate_stream_indices = self.get_audio_track_candidates()
    if candidate_stream_indices and target_stream_index not in candidate_stream_indices:
        candidate_stream_indices = candidate_stream_indices + (target_stream_index,)

    try:
        ok = bool(adapter_selector(target_stream_index, candidate_stream_indices or None))
    except ADAPTER_EXCEPTIONS:
        logger.debug('[VideoController] select_audio_stream(%s) failed', target_stream_index, exc_info=True)
        return False

    if ok:
        self._current_audio_stream_index = target_stream_index
    return ok



def _bootstrap_runtime_audio_track_inventory(self) -> tuple[int, ...]:
    """Bootstrap generic audio candidates when external probe metadata is unavailable.

    Edge cases:
        1. User environments can lack ffprobe, so the controller still needs bounded runtime candidates for fallback and manual selection.
        2. Stream-count queries can fail or report only the primary video stream, in which case the bootstrap must keep the current empty inventory.
        3. Runtime-only inventories have no language/codec metadata, so descriptors must stay generic but stable for the UI layer.
    """
    existing_candidates = self.get_audio_track_candidates()
    existing_descriptors = self.get_audio_track_descriptors() if hasattr(self, 'get_audio_track_descriptors') else ()
    if existing_candidates and existing_descriptors:
        return existing_candidates

    if not self._adapter:
        return existing_candidates

    stream_counter = getattr(self._adapter, 'get_number_of_streams', None)
    if not callable(stream_counter):
        return existing_candidates

    try:
        stream_count = max(0, int(stream_counter() or 0))
    except ADAPTER_EXCEPTIONS:
        logger.debug('[VideoController] get_number_of_streams failed during runtime audio bootstrap', exc_info=True)
        return existing_candidates

    if stream_count <= 1:
        return existing_candidates

    generic_candidates = tuple(range(1, stream_count))
    if not generic_candidates:
        return existing_candidates

    if not existing_candidates:
        existing_candidates = set_audio_track_candidates(self, generic_candidates)
        logger.info(
            '[VideoController] Bootstrapped generic runtime audio candidates (missing probe metadata): %s',
            existing_candidates,
        )

    if not existing_descriptors and hasattr(self, 'set_audio_track_descriptors'):
        generic_descriptors = tuple(
            {
                'stream_index': stream_index,
                'track_index': ordinal,
                'language': '',
                'title': '',
                'codec_name': '',
                'codec_long_name': '',
                'channels': None,
                'channel_layout': '',
                'is_default': ordinal == 0,
                'is_forced': False,
                'label': f'Track {ordinal + 1} — stream #{stream_index}',
            }
            for ordinal, stream_index in enumerate(existing_candidates)
        )
        try:
            self.set_audio_track_descriptors(generic_descriptors)
        except ADAPTER_EXCEPTIONS:
            logger.debug('[VideoController] Failed caching generic runtime audio descriptors', exc_info=True)

    return existing_candidates


def _prime_audio_track_candidates(self) -> int | None:
    """Prime the first candidate audio stream, when available, before the initial play attempt.

    Edge cases:
        1. Older adapters can lack the priming API and must leave playback behavior unchanged.
        2. Empty candidate lists must not trigger adapter calls or mutate controller state.
        3. Adapter priming failures must degrade cleanly so playback can still try the default engine state.
    """
    if not self._adapter:
        return None

    candidate_stream_indices = self.get_audio_track_candidates()
    if not candidate_stream_indices:
        return None

    adapter_primer = getattr(self._adapter, 'prime_audio_stream_candidates', None)
    if not callable(adapter_primer):
        return None

    try:
        primed_stream_index = adapter_primer(candidate_stream_indices)
    except ADAPTER_EXCEPTIONS:
        logger.debug('[VideoController] prime_audio_stream_candidates failed', exc_info=True)
        return None

    try:
        normalized_stream_index = int(primed_stream_index)
    except FORMAT_EXCEPTIONS:
        return None

    if normalized_stream_index < 0:
        return None

    self._current_audio_stream_index = normalized_stream_index
    return normalized_stream_index


def _finalize_runtime_audio_track_state_after_start(self) -> None:
    """Bootstrap runtime audio-track data only after the Media Engine exposes stable metadata.

    Edge cases:
        1. Freshly loaded sources can report duration=0 while metadata is still warming up, so the helper must retry only for a tiny bounded window.
        2. Legacy adapters can miss pump_events/get_duration, in which case the helper must skip runtime bootstrap instead of forcing unsafe stream queries.
        3. Shutdown or adapter teardown can happen during the warm-up loop, so every iteration must fail fast without mutating stale session state.
    """
    if not self._adapter or self._shutting_down:
        return

    adapter_pump = getattr(self._adapter, 'pump_events', None)
    adapter_get_duration = getattr(self._adapter, 'get_duration', None)
    if not callable(adapter_get_duration):
        return

    duration = 0.0
    for _ in range(4):
        if self._shutting_down or not self._adapter:
            return
        if callable(adapter_pump):
            try:
                adapter_pump()
            except ADAPTER_EXCEPTIONS:
                logger.debug('[VideoController] pump_events failed during runtime audio-track warm-up', exc_info=True)
                return
        try:
            duration = max(0.0, float(adapter_get_duration() or 0.0))
        except ADAPTER_EXCEPTIONS:
            logger.debug('[VideoController] get_duration failed during runtime audio-track warm-up', exc_info=True)
            return
        if duration > 0.0:
            break
        time.sleep(0.01)

    if duration <= 0.0:
        logger.debug('[VideoController] Skipping runtime audio-track bootstrap until video metadata is ready.')
        return

    _bootstrap_runtime_audio_track_inventory(self)
    if self._current_audio_stream_index is None:
        _prime_audio_track_candidates(self)



def _retry_play_with_audio_fallback(self, initial_error: Exception) -> None:
    """Retry video playback with alternative audio streams in a bounded deterministic order.

    Edge cases:
        1. Retry loops must stay bounded even if metadata repeats the same candidate stream index.
        2. Adapter stop or select failures must not mask the original playback error unless a later retry gives better context.
        3. Legacy adapters without audio-stream APIs must re-raise the initial error immediately instead of pretending fallback exists.
    """
    if not self._adapter:
        raise initial_error

    candidate_stream_indices = self.get_audio_track_candidates()
    if len(candidate_stream_indices) <= 1:
        raise initial_error

    selected_candidates = set(self.get_selected_audio_streams())
    current_stream_index = getattr(self, '_current_audio_stream_index', None)
    try:
        if current_stream_index is not None:
            selected_candidates.add(int(current_stream_index))
    except FORMAT_EXCEPTIONS:
        logger.debug('[VideoController] Ignoring invalid current audio stream index: %r', current_stream_index)

    last_error: Exception = initial_error
    for candidate_stream_index in candidate_stream_indices:
        if candidate_stream_index in selected_candidates:
            continue

        try:
            self._adapter.stop()
        except ADAPTER_EXCEPTIONS:
            logger.debug('[VideoController] stop() failed during audio fallback retry.', exc_info=True)

        if not self.select_audio_stream(candidate_stream_index):
            continue

        selected_candidates.add(candidate_stream_index)
        try:
            self._adapter.play()
            logger.info(
                '[VideoController] Playback recovered by switching to audio stream %s',
                candidate_stream_index,
            )
            return
        except ADAPTER_EXCEPTIONS as exc:
            last_error = exc
            logger.warning(
                '[VideoController] Audio fallback stream %s failed: %s',
                candidate_stream_index,
                exc,
                exc_info=True,
            )

    raise last_error



def play_media(self, path: str, duration_hint: float = 0.0) -> None:
    """
    Carica e avvia il video nel player MF.
    Si assume che l'adapter sia già stato preparato
    tramite ensure_video_adapter().
    """
    if self._shutting_down:
        logger.debug('[VideoController] play_media ignored (shutting down)')
        return

    if not self._adapter:
        logger.error(
            '[VideoController] play_media called without adapter '
            '(did you call ensure_video_adapter() first?)'
        )
        return

    logger.info('[VideoController] play_media(path=%s)', path)
    self._current_path = path
    try:
        self._current_duration_hint = max(0.0, float(duration_hint or 0.0))
    except FORMAT_EXCEPTIONS:
        self._current_duration_hint = 0.0

    try:
        # A new source on the same HWND still needs a fresh viewport update once
        # the UI republishes the current surface geometry for this playback session.
        self._last_surface_signature = None
        self._publish_event(
            AudioEventType.VIDEO_PLAYBACK_STARTING,
            {'hwnd': self._current_hwnd, 'path': path},
        )

        self._current_audio_stream_index = None
        self._adapter.load_source(path)
        try:
            self._adapter.play()
        except ADAPTER_EXCEPTIONS as exc:
            _prime_audio_track_candidates(self)
            _retry_play_with_audio_fallback(self, exc)

        _finalize_runtime_audio_track_state_after_start(self)

        try:
            self._adapter.set_volume(self._volume)
        except ADAPTER_EXCEPTIONS:
            logger.debug('[VideoController] adapter.set_volume failed after play_media', exc_info=True)

        self._update_state(VideoState.PLAYING)
        self._publish_event(
            AudioEventType.VIDEO_PLAYBACK_STARTED,
            {'hwnd': self._current_hwnd, 'path': path},
        )
    except ADAPTER_EXCEPTIONS as exc:
        logger.error('[VideoController] Failed to play media: %s', exc, exc_info=True)
        self._update_state(VideoState.ERROR)
        self._publish_event(
            AudioEventType.VIDEO_PLAYBACK_ERROR,
            {'hwnd': self._current_hwnd, 'path': path, 'error': str(exc)},
        )



def pause(self) -> None:
    """Mette in pausa il video corrente, se possibile."""
    if self._adapter and self._state == VideoState.PLAYING:
        try:
            logger.debug('[VideoController] pause() requested')
            self._adapter.pause()
            self._update_state(VideoState.PAUSED)
            self._publish_event(AudioEventType.VIDEO_PLAYBACK_PAUSED, {})
        except ADAPTER_EXCEPTIONS as exc:
            logger.error('[VideoController] Failed to pause video: %s', exc, exc_info=True)



def resume(self) -> None:
    """Riprende la riproduzione del video corrente, se possibile."""
    if self._adapter and self._state == VideoState.PAUSED:
        try:
            logger.debug('[VideoController] resume() requested')
            self._adapter.resume()
            self._update_state(VideoState.PLAYING)
            self._publish_event(AudioEventType.VIDEO_PLAYBACK_RESUMED, {})
        except ADAPTER_EXCEPTIONS as exc:
            logger.error('[VideoController] Failed to resume video: %s', exc, exc_info=True)



def stop(self, close_adapter: bool = False) -> None:
    """
    Ferma la riproduzione attuale.
    Se close_adapter=True chiude anche l'adapter video.
    """
    if self._shutting_down:
        if close_adapter:
            self._close_adapter_internal()
        return

    if close_adapter:
        self._close_adapter_internal()
        return

    if self._adapter and self._state in (VideoState.PLAYING, VideoState.PAUSED):
        try:
            logger.debug('[VideoController] stop(close_adapter=%s) requested', close_adapter)
            self._adapter.stop()
            self._update_state(VideoState.STOPPED)
        except ADAPTER_EXCEPTIONS as exc:
            logger.warning('[VideoController] stop() failed: %s', exc, exc_info=True)

    self._publish_event(
        AudioEventType.VIDEO_PLAYBACK_STOPPED,
        {},
        require_ui_thread=True,
    )



def set_volume(self, volume: float) -> None:
    """Imposta il volume del video."""
    try:
        normalized_volume = max(0.0, min(1.0, float(volume)))
    except FORMAT_EXCEPTIONS:
        normalized_volume = 1.0
    self._volume = normalized_volume

    if self._adapter:
        try:
            logger.debug('[VideoController] set_volume(%.3f)', normalized_volume)
            self._adapter.set_volume(normalized_volume)
        except ADAPTER_EXCEPTIONS as exc:
            logger.error('[VideoController] Failed to set volume: %s', exc, exc_info=True)



def get_duration(self) -> float:
    """Ritorna la durata del media corrente."""
    duration = 0.0
    if self._adapter:
        try:
            duration = float(self._adapter.get_duration())
        except ADAPTER_EXCEPTIONS:
            duration = 0.0

    if duration > 0.0:
        return duration
    return max(0.0, float(self._current_duration_hint or 0.0))



def get_position(self) -> float:
    """Ritorna la posizione corrente del media."""
    if not self._adapter:
        return 0.0
    try:
        return float(self._adapter.get_position())
    except ADAPTER_EXCEPTIONS:
        return 0.0



def seek(self, position_sec: float) -> bool:
    """Cerca all'interno del video."""
    if not self._adapter:
        return False
    try:
        self._adapter.seek(float(position_sec))
        return True
    except ADAPTER_EXCEPTIONS as exc:
        logger.debug('[VideoController] seek(%s) failed: %s', position_sec, exc, exc_info=True)
        return False



def poll_end(self) -> bool:
    """
    Va chiamato periodicamente dal PlayerController.
    Se la riproduzione è terminata e loop è OFF:
     - chiude l'adapter
     - ritorna True (per aggiornare stato player)
    Se non è terminata o loop è ON: ritorna False.
    """
    if not self._adapter:
        return False

    try:
        self._adapter.pump_events()
    except ADAPTER_EXCEPTIONS as exc:
        logger.warning('[VideoController] pump_events() failed: %s', exc, exc_info=True)

    try:
        ended = self._adapter.has_ended()
    except ADAPTER_EXCEPTIONS as exc:
        logger.debug('[VideoController] has_ended() check failed: %s', exc, exc_info=True)
        return False

    if not ended:
        return False

    logger.info('[VideoController] Video ended (loop=%s)', self._loop_enabled)

    if self._loop_enabled:
        return False

    self._publish_event(
        AudioEventType.VIDEO_PLAYBACK_ENDED,
        {
            'path': self._current_path,
            'position': self.get_position(),
            'duration': self.get_duration(),
        },
        require_ui_thread=True,
    )

    self._close_adapter_internal()
    return True



_VIDEO_CONTROLLER_PLAYBACK_METHODS: tuple[tuple[str, Callable[..., Any]], ...] = (
    ("get_audio_track_candidates", get_audio_track_candidates),
    ("set_audio_track_candidates", set_audio_track_candidates),
    ("get_selected_audio_streams", get_selected_audio_streams),
    ("select_audio_stream", select_audio_stream),
    ("play_media", play_media),
    ("pause", pause),
    ("resume", resume),
    ("stop", stop),
    ("set_volume", set_volume),
    ("get_duration", get_duration),
    ("get_position", get_position),
    ("seek", seek),
    ("poll_end", poll_end),
)


def install_video_controller_playback_behavior(controller_cls: type) -> None:
    """Install video controller playback behavior on the central coordinator.

    Edge cases:
        1. A binding name is empty and would overwrite an unintended attribute.
        2. Duplicate playback names silently shadow an earlier controller method.
        3. A non-callable binding reaches class wiring and breaks runtime behavior.
    """
    if getattr(controller_cls, '_video_controller_playback_behavior_attached', False):
        return

    seen_names: set[str] = set()
    for attribute_name, method in _VIDEO_CONTROLLER_PLAYBACK_METHODS:
        if not attribute_name:
            raise TypeError('Video controller playback binding name cannot be empty')
        if attribute_name in seen_names:
            raise TypeError(f'Duplicate video controller playback binding: {attribute_name}')
        if not callable(method):
            raise TypeError(f'Invalid video controller playback binding: {attribute_name}')
        setattr(controller_cls, attribute_name, method)
        seen_names.add(attribute_name)

    setattr(controller_cls, '_video_controller_playback_behavior_attached', True)


attach_video_controller_playback_behavior = install_video_controller_playback_behavior
