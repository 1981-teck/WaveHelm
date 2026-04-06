from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Tuple

import pygame

from src.audio.audio_events import AudioEventType

logger = logging.getLogger(__name__)

EVENT_BUS_EXCEPTIONS: Tuple[type[BaseException], ...] = (
    AttributeError,
    RuntimeError,
    TypeError,
)
MIXER_EXCEPTIONS: Tuple[type[BaseException], ...] = (AttributeError, pygame.error)


def publish_bus_event(
    self,
    event_type: AudioEventType,
    payload: Dict[str, Any],
    *,
    context: str,
    level: str = 'error',
) -> bool:
    try:
        self.event_bus.publish(event_type, payload)
        return True
    except EVENT_BUS_EXCEPTIONS as exc:
        log_fn = getattr(logger, level, logger.error)
        log_fn('%s: %s', context, exc)
        return False



def safe_unsubscribe(self, event_type: AudioEventType, callback: Any, *, label: str) -> None:
    try:
        self.event_bus.unsubscribe(event_type, callback=callback)
    except EVENT_BUS_EXCEPTIONS as exc:
        logger.error('Failed to unsubscribe from %s: %s', label, exc)



def safe_quit_mixer() -> None:
    try:
        if pygame.mixer.get_init():
            pygame.mixer.quit()
    except MIXER_EXCEPTIONS as exc:
        logger.error('Failed to quit mixer: %s', exc)



def prepare_file_selection(self, path_obj: Path) -> None:
    self._cancel_dsp_refresh_timer()
    self._current_file = str(path_obj)
    self._playback_source_file = None
    self._is_music_loaded = False
    self._current_position = 0.0
    self._seek_base = 0.0
    self._total_length = 0.0
    self._video_spectrum_feedback_sent = False
    self._no_file_spectrum_feedback_sent = False
    self._current_play_uses_native_loop = False



def handle_video_selection(self, path_obj: Path) -> bool:
    logger.info('Video detected: Releasing Pygame mixer for VideoController.')
    try:
        if pygame.mixer.get_init():
            pygame.mixer.quit()
    except MIXER_EXCEPTIONS as exc:
        logger.error('Failed to release mixer for video selection: %s', exc)

    publish_bus_event(
        self,
        AudioEventType.MEDIA_LOADED,
        {'path': str(path_obj), 'media_type': 'video', 'duration': 0.0},
        context='Failed to publish MEDIA_LOADED for video selection',
    )
    publish_bus_event(
        self,
        AudioEventType.PLAYER_STATE_CHANGED,
        {
            'is_playing': False,
            'is_paused': False,
            'current_media_path': str(path_obj),
            'state': 'STOPPED',
        },
        context='Failed to publish PLAYER_STATE_CHANGED for video selection',
    )
    publish_bus_event(
        self,
        AudioEventType.FEEDBACK_MESSAGE,
        {
            'message': self.localization_manager.get_text(
                'video_detected_will_use_videoplayer',
                default='Video detected: playback handled by VideoController.',
            ),
            'color': 'blue',
        },
        context='Failed to publish feedback for video selection',
    )
    return False



def publish_audio_loaded_events(self) -> None:
    publish_bus_event(
        self,
        AudioEventType.MEDIA_LOADED,
        {
            'path': self._current_file,
            'media_type': 'audio',
            'duration': self._total_length,
        },
        context='Failed to publish MEDIA_LOADED for audio file',
    )
    publish_bus_event(
        self,
        AudioEventType.MEDIA_DURATION_UPDATE,
        {'duration': self._total_length, 'path': self._current_file},
        context='Failed to publish MEDIA_DURATION_UPDATE for audio file',
    )
    publish_bus_event(
        self,
        AudioEventType.PLAYER_STATE_CHANGED,
        {
            'position': 0.0,
            'duration': self._total_length,
            'is_playing': False,
            'is_paused': False,
        },
        context='Failed to publish PLAYER_STATE_CHANGED for audio file',
    )



def publish_video_playback_feedback(self) -> None:
    publish_bus_event(
        self,
        AudioEventType.FEEDBACK_MESSAGE,
        {
            'message': self._get_localized_text(
                'video_detected_will_use_videoplayer',
                default='Video detected: playback handled by VideoController.',
            ),
            'color': 'blue',
        },
        context='Failed to publish video playback redirect feedback',
    )



def start_mixer_playback(self, *, start_pos: float, play_loops: int) -> bool:
    try:
        pygame.mixer.music.set_volume(0.0 if self._is_muted else self._volume)
    except MIXER_EXCEPTIONS as exc:
        logger.error('Failed to set volume on play: %s', exc)

    try:
        pygame.mixer.music.play(loops=play_loops, start=start_pos)
        self._seek_base = start_pos
        self._is_paused = False
        self._current_play_uses_native_loop = bool(play_loops == -1)
        return True
    except pygame.error as exc:
        logger.error('Pygame error on play: %s. Music may not be loaded correctly', exc)
        self._is_music_loaded = False
        publish_bus_event(
            self,
            AudioEventType.FEEDBACK_MESSAGE,
            {
                'message': self._get_localized_text('error_playing_file').format(file=self._current_file),
                'color': 'red',
            },
            context='Failed to publish playback error feedback',
        )
        return False



def restore_playback_after_seek(self, position_sec: float, play_loops: int) -> None:
    self._seek_base = position_sec
    try:
        pygame.mixer.music.play(loops=play_loops, start=position_sec)
        return
    except TypeError:
        pass

    try:
        pygame.mixer.music.set_pos(position_sec)
        if not pygame.mixer.music.get_busy():
            pygame.mixer.music.play(loops=play_loops)
    except (pygame.error, TypeError, ValueError, AttributeError):
        pygame.mixer.music.stop()
        pygame.mixer.music.play(loops=play_loops)
        self._seek_base = 0.0
        self._current_position = 0.0



def reset_stopped_state(self) -> None:
    self._current_position = 0.0
    self._seek_base = 0.0
    self._current_file = None
    self._playback_source_file = None
    self._is_paused = False
    self._current_play_uses_native_loop = False
    self._video_spectrum_feedback_sent = False
    self._no_file_spectrum_feedback_sent = False
