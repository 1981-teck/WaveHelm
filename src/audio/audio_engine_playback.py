from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Tuple

import pygame
import soundfile as sf

from src.audio.audio_events import AudioEventType
from src.utils.media_metadata import (
    AudioMetadataDependencyUnavailableError,
    AudioMetadataError,
    read_audio_basic_metadata,
)

from src.audio.audio_engine_shared import VIDEO_EXTS, normalize_loop_position
from src.audio.audio_engine_playback_support import (
    EVENT_BUS_EXCEPTIONS,
    MIXER_EXCEPTIONS,
    handle_video_selection,
    prepare_file_selection,
    publish_audio_loaded_events,
    publish_bus_event,
    publish_video_playback_feedback,
    reset_stopped_state,
    restore_playback_after_seek,
    safe_quit_mixer,
    safe_unsubscribe,
    start_mixer_playback,
)

logger = logging.getLogger(__name__)

DURATION_EXCEPTIONS: Tuple[type[BaseException], ...] = (
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


def _publish_bus_event(
    self,
    event_type: AudioEventType,
    payload: Dict[str, Any],
    *,
    context: str,
    level: str = 'error',
) -> bool:
    return publish_bus_event(self, event_type, payload, context=context, level=level)



def _safe_unsubscribe(self, event_type: AudioEventType, callback: Any, *, label: str) -> None:
    safe_unsubscribe(self, event_type, callback, label=label)



def _safe_quit_mixer() -> None:
    safe_quit_mixer()



def _resolve_play_loops(self) -> int:
    """Return the pygame loop count for the current repeat setting."""
    return -1 if bool(getattr(self, '_loop_enabled', False)) else 0



def _should_normalize_loop_position(self) -> bool:
    return bool(getattr(self, '_current_play_uses_native_loop', False))



def _estimate_metadata_duration_seconds(source_path: Path) -> float:
    """Estimate duration from audio metadata when soundfile cannot provide it.

    Edge cases handled:
    - TinyTag may be missing from the active runtime environment, so dependency failures fall back to 0.0
    - malformed metadata or unsupported files must not break playback selection
    - metadata readers can report negative or non-numeric duration values on bad files
    """
    try:
        return max(0.0, read_audio_basic_metadata(str(source_path)).duration)
    except (
        AudioMetadataDependencyUnavailableError,
        AudioMetadataError,
        FileNotFoundError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        logger.debug('TinyTag metadata failed to get duration: %s', exc)
        return 0.0


def _estimate_duration_seconds(playback_path: str, source_path: Path) -> float:
    """Return the best-effort duration for the current playback target.

    Edge cases handled:
    - decoded playback paths may differ from the original source path
    - soundfile can fail on unsupported/corrupted files or report a zero samplerate
    - metadata fallback must fail closed to 0.0 instead of aborting playback setup
    """
    duration = 0.0

    try:
        info = sf.info(playback_path)
        duration = float(info.frames) / float(info.samplerate) if info.samplerate else 0.0
    except DURATION_EXCEPTIONS as exc:
        logger.debug('Soundfile failed to get duration: %s', exc)

    if duration == 0.0:
        duration = _estimate_metadata_duration_seconds(source_path)

    return max(0.0, duration)



def set_file(self, file_path: str) -> bool:
    """Set the current file and return True if loaded successfully."""
    path_obj = Path(file_path)
    prepare_file_selection(self, path_obj)

    if path_obj.suffix.lower() in VIDEO_EXTS:
        return handle_video_selection(self, path_obj)

    if not self._ensure_audio_mixer():
        return False

    with self._dsp_render_lock:
        if not self._load_audio_source(path_obj):
            return False

    info_path = self._playback_source_file or str(path_obj)
    self._total_length = _estimate_duration_seconds(info_path, path_obj)
    logger.debug('Calculated duration for %s: %.2f seconds', path_obj, self._total_length)
    publish_audio_loaded_events(self)
    return True



def _on_video_duration_update(self, data: Dict[str, Any]) -> None:
    """Handle video duration updates from the VideoController."""
    try:
        new_duration = float(data.get('duration', 0.0))
    except (TypeError, ValueError) as exc:
        logger.warning('Invalid duration update: %s', exc)
        return

    if new_duration <= 0:
        return

    self._total_length = new_duration
    logger.debug('Updated duration from VideoController: %.2f', new_duration)
    _publish_bus_event(
        self,
        AudioEventType.MEDIA_DURATION_UPDATE,
        {'duration': self._total_length},
        context='Failed to publish duration update',
    )



def close(self) -> None:
    """Close the audio engine and clean up resources."""
    _safe_unsubscribe(self, AudioEventType.VIDEO_DURATION_UPDATE, self._on_video_duration_update, label='VIDEO_DURATION_UPDATE')
    _safe_unsubscribe(self, AudioEventType.EQ_CHANGED, self._on_eq_changed, label='EQ_CHANGED')
    _safe_unsubscribe(self, AudioEventType.EFFECTS_CHANGED, self._on_effects_changed, label='EFFECTS_CHANGED')
    _safe_unsubscribe(self, AudioEventType.VOLUME_CHANGED, self._on_volume_changed_event, label='VOLUME_CHANGED')
    _safe_unsubscribe(self, AudioEventType.SETTINGS_BATCH_UPDATED, self._on_settings_batch_updated, label='SETTINGS_BATCH_UPDATED')

    self._stop_progress_loop()
    self.stop()
    self._cancel_dsp_refresh_timer()
    self._close_soundfile()
    _safe_quit_mixer()
    cleanup_processed = getattr(self, '_cleanup_processed_audio_dir', None)
    if callable(cleanup_processed):
        try:
            cleanup_processed()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            logger.debug('Processed audio cleanup failed during audio engine close.', exc_info=True)
    logger.info('Audio engine closed correctly')



def play(self) -> None:
    """Start or resume playback."""
    if not self._current_file:
        logger.warning('play() called without a loaded file')
        _publish_bus_event(
            self,
            AudioEventType.FEEDBACK_MESSAGE,
            {'message': self._get_localized_text('no_media_loaded'), 'color': 'orange'},
            context='Failed to publish no media feedback',
        )
        return

    if Path(self._current_file).suffix.lower() in VIDEO_EXTS:
        logger.info('play() ignored for video file; VideoController should handle playback')
        publish_video_playback_feedback(self)
        return

    start_pos = max(0.0, float(self._current_position))
    play_loops = _resolve_play_loops(self)
    try:
        if not start_mixer_playback(self, start_pos=start_pos, play_loops=play_loops):
            return
    except (TypeError, ValueError) as exc:
        logger.error('Invalid playback start state: %s', exc, exc_info=True)
        raise

    self._start_progress_loop()
    _publish_bus_event(
        self,
        AudioEventType.PLAYBACK_STARTED,
        {'path': self._current_file, 'position': self._current_position},
        context='Failed to publish PLAYBACK_STARTED',
    )
    _publish_bus_event(
        self,
        AudioEventType.PLAYER_STATE_CHANGED,
        {'is_playing': True, 'is_paused': False},
        context='Failed to publish PLAYER_STATE_CHANGED on play',
    )



def pause(self) -> None:
    """Pause playback."""
    try:
        pos_ms = pygame.mixer.music.get_pos() if pygame.mixer.get_init() else -1
        if pos_ms is not None and pos_ms >= 0:
            raw_position = self._seek_base + float(pos_ms) / 1000.0
            self._current_position = normalize_loop_position(raw_position, self._total_length, _should_normalize_loop_position(self))
    except (pygame.error, TypeError, ValueError, AttributeError) as exc:
        logger.error('Failed to get position on pause: %s', exc)

    try:
        pygame.mixer.music.pause()
        self._is_paused = True
    except MIXER_EXCEPTIONS as exc:
        logger.error('Failed to pause: %s', exc)

    self._stop_progress_loop()
    _publish_bus_event(self, AudioEventType.PLAYBACK_PAUSED, {}, context='Failed to publish PLAYBACK_PAUSED')
    _publish_bus_event(
        self,
        AudioEventType.PLAYER_STATE_CHANGED,
        {'is_playing': False, 'is_paused': True},
        context='Failed to publish PLAYER_STATE_CHANGED on pause',
    )



def resume(self) -> None:
    """Resume playback."""
    try:
        if pygame.mixer.get_init() and not pygame.mixer.music.get_busy():
            resume_pos = max(0.0, float(self._current_position))
            play_loops = _resolve_play_loops(self)
            pygame.mixer.music.play(loops=play_loops, start=resume_pos)
            self._seek_base = resume_pos
            self._current_play_uses_native_loop = bool(play_loops == -1)
        else:
            pygame.mixer.music.unpause()
        self._is_paused = False
    except (pygame.error, TypeError, ValueError, AttributeError) as exc:
        logger.error('Failed to unpause: %s', exc)

    self._start_progress_loop()
    _publish_bus_event(self, AudioEventType.PLAYBACK_RESUMED, {}, context='Failed to publish PLAYBACK_RESUMED')
    _publish_bus_event(
        self,
        AudioEventType.PLAYER_STATE_CHANGED,
        {'is_playing': True, 'is_paused': False},
        context='Failed to publish PLAYER_STATE_CHANGED on resume',
    )



def stop(self) -> None:
    """Stop playback and reset state."""
    try:
        pygame.mixer.music.stop()
    except MIXER_EXCEPTIONS as exc:
        logger.error('Failed to stop: %s', exc)

    reset_stopped_state(self)
    self._cancel_dsp_refresh_timer()
    self._stop_progress_loop()
    self._close_soundfile()
    _publish_bus_event(self, AudioEventType.PLAYBACK_STOPPED, {}, context='Failed to publish PLAYBACK_STOPPED')
    _publish_bus_event(
        self,
        AudioEventType.PLAYER_STATE_CHANGED,
        {'is_playing': False, 'is_paused': False},
        context='Failed to publish PLAYER_STATE_CHANGED on stop',
    )



def is_playing(self) -> bool:
    """Check if currently playing."""
    try:
        if not pygame.mixer.get_init() or self._is_paused:
            return False
        return bool(pygame.mixer.music.get_busy())
    except MIXER_EXCEPTIONS as exc:
        logger.debug('Non-critical is_playing check error: %s', exc)
        return False



def is_paused(self) -> bool:
    """Check if currently paused."""
    return self._is_paused



def get_length(self) -> float:
    """Get the total length of the current media in seconds."""
    return float(self._total_length or 0.0)



def get_duration(self) -> float:
    """Alias for get_length."""
    return self.get_length()



def get_position(self) -> float:
    """Get the current playback position in seconds."""
    try:
        if not pygame.mixer.get_init():
            return float(self._current_position or 0.0)
        pos_ms = pygame.mixer.music.get_pos() if pygame.mixer.get_init() else -1
        if pos_ms is None or pos_ms < 0:
            return float(self._current_position or 0.0)
        raw_position = self._seek_base + (float(pos_ms) / 1000.0)
        return normalize_loop_position(raw_position, self._total_length, _should_normalize_loop_position(self))
    except (pygame.error, TypeError, ValueError, AttributeError) as exc:
        logger.error('Failed to get position: %s', exc)
        return float(self._current_position or 0.0)



def seek(self, position_sec: float) -> None:
    """Seek to the specified position in seconds."""
    position_sec = max(0.0, float(position_sec))
    self._current_position = position_sec

    play_loops = _resolve_play_loops(self)
    try:
        restore_playback_after_seek(self, position_sec, play_loops)
        self._current_play_uses_native_loop = bool(play_loops == -1)
        self._start_progress_loop()
        _publish_bus_event(
            self,
            AudioEventType.SEEK,
            {'position': position_sec},
            context='Failed to publish SEEK event',
        )
    except (pygame.error, TypeError, ValueError, AttributeError) as exc:
        logger.warning('Seek failed: %s', exc, exc_info=True)
