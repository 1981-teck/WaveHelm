from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pygame

from src.audio.audio_events import AudioEventType
from src.audio.audio_engine_shared import VIDEO_EXTS, normalize_loop_position

logger = logging.getLogger(__name__)

EVENT_BUS_EXCEPTIONS: Tuple[type[BaseException], ...] = (
    AttributeError,
    RuntimeError,
    TypeError,
)
MIXER_EXCEPTIONS: Tuple[type[BaseException], ...] = (AttributeError, pygame.error)
SPECTRUM_EXCEPTIONS: Tuple[type[BaseException], ...] = (
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


def _publish_bus_event(self, event_type: AudioEventType, payload: Dict[str, Any], *, context: str, level: str = 'error') -> bool:
    try:
        self.event_bus.publish(event_type, payload)
        return True
    except EVENT_BUS_EXCEPTIONS as exc:
        getattr(logger, level, logger.error)('%s: %s', context, exc)
        return False


def _start_progress_loop(self) -> None:
    """Start the progress update loop in a background thread."""
    if self._progress_thread and self._progress_thread.is_alive():
        return

    self._progress_stop_event.clear()
    self._was_playing = False

    def _loop():
        last_progress_publish = time.perf_counter() - float(self._progress_publish_interval)
        last_spectrum_publish = time.perf_counter() - float(self._spectrum_publish_interval)
        try:
            while not self._progress_stop_event.is_set():
                now = time.perf_counter()
                busy = False
                try:
                    busy = bool(pygame.mixer.get_init() and pygame.mixer.music.get_busy())
                except MIXER_EXCEPTIONS as exc:
                    logger.debug('[poll] busy state not available: %s', exc)
                    busy = False

                try:
                    pos_ms = pygame.mixer.music.get_pos() if pygame.mixer.get_init() else -1
                    if pos_ms is not None and pos_ms >= 0:
                        raw_position = self._seek_base + float(pos_ms) / 1000.0
                        self._current_position = normalize_loop_position(
                            raw_position,
                            self._total_length,
                            bool(getattr(self, '_current_play_uses_native_loop', False)),
                        )
                except (pygame.error, TypeError, ValueError, AttributeError) as exc:
                    logger.debug('[poll] position not available in progress loop: %s', exc)

                try:
                    current = float(self._current_position or 0.0)
                    duration = float(self._total_length or 0.0)
                    percent = (current / duration * 100.0) if duration > 0 else 0.0
                    if now - last_progress_publish >= float(self._progress_publish_interval):
                        _publish_bus_event(
                            self,
                            AudioEventType.PLAYBACK_PROGRESS,
                            {
                                'current_time': current,
                                'total_duration': duration,
                                'progress_percent': percent,
                                'position': current,
                                'duration': duration,
                                'path': self._current_file,
                            },
                            context='Failed to publish PLAYBACK_PROGRESS',
                        )
                        last_progress_publish = now

                    if (
                        self._sf_file
                        and self._sf_file.seekable()
                        and (now - last_spectrum_publish >= float(self._spectrum_publish_interval))
                    ):
                        try:
                            frame_offset = int(current * self._sf_file.samplerate)
                            self._sf_file.seek(frame_offset)
                            audio_chunk = self._sf_file.read(self.audio_analyzer.fft_size, dtype='float32')
                            if len(audio_chunk) < self.audio_analyzer.fft_size:
                                audio_chunk = np.pad(
                                    audio_chunk,
                                    (0, self.audio_analyzer.fft_size - len(audio_chunk)),
                                    'constant',
                                )
                            spectrum_data = self.audio_analyzer.analyze(audio_chunk)
                            _publish_bus_event(
                                self,
                                AudioEventType.SPECTRUM_DATA_UPDATED,
                                {'spectrum_data': spectrum_data.tolist()},
                                context='Failed to publish SPECTRUM_DATA_UPDATED',
                                level='debug',
                            )
                            last_spectrum_publish = now
                        except SPECTRUM_EXCEPTIONS as exc:
                            logger.debug('Failed to get or publish spectrum data: %s', exc)
                    elif self._current_file and Path(self._current_file).suffix.lower() in VIDEO_EXTS:
                        if not getattr(self, '_video_spectrum_feedback_sent', False):
                            _publish_bus_event(
                                self,
                                AudioEventType.FEEDBACK_MESSAGE,
                                {
                                    'message': self._get_localized_text(
                                        'spectrum_not_available_for_video',
                                        default='Spectrum data not available for video files.',
                                    ),
                                    'color': 'orange',
                                },
                                context='Failed to publish video spectrum feedback',
                            )
                            self._video_spectrum_feedback_sent = True
                    elif not self._current_file:
                        if not getattr(self, '_no_file_spectrum_feedback_sent', False):
                            _publish_bus_event(
                                self,
                                AudioEventType.FEEDBACK_MESSAGE,
                                {
                                    'message': self._get_localized_text(
                                        'spectrum_no_file_loaded',
                                        default='No file loaded for spectrum analysis.',
                                    ),
                                    'color': 'orange',
                                },
                                context='Failed to publish empty spectrum feedback',
                            )
                            self._no_file_spectrum_feedback_sent = True

                except (ValueError, TypeError, RuntimeError) as exc:
                    logger.error('Failed to publish progress: %s', exc)

                if self._was_playing and not busy:
                    _publish_bus_event(
                        self,
                        AudioEventType.PLAYBACK_COMPLETED,
                        {'path': self._current_file},
                        context='Failed to publish PLAYBACK_COMPLETED',
                    )
                    break

                self._was_playing = busy
                if self._progress_stop_event.wait(float(self._poll_sleep_interval)):
                    break
        except (RuntimeError, TypeError, ValueError, OSError, AttributeError) as exc:
            logger.error('Progress loop error: %s', exc)

    self._progress_thread = threading.Thread(target=_loop, daemon=True)
    self._progress_thread.start()


def _stop_progress_loop(self) -> None:
    """Stop the progress update loop."""
    self._progress_stop_event.set()
    thread = self._progress_thread
    if thread and thread.is_alive():
        try:
            thread.join(timeout=0.5)
        except RuntimeError as exc:
            logger.error('Failed to join progress thread: %s', exc)
    self._progress_thread = None
    self._was_playing = False


def set_volume(self, volume: float) -> None:
    """Set the volume level (0.0 to 1.0).

    Edge cases handled:
    - Invalid or non-numeric input is coerced to a deterministic in-range float.
    - Repeated updates within the debounce threshold avoid redundant mixer/event churn.
    - Missing or failing settings backends must not block runtime volume changes.
    """
    value = max(0.0, min(1.0, float(volume)))
    if abs(self._volume - value) < 0.01:
        return

    self._volume = value
    try:
        settings_manager = getattr(self, 'settings_manager', None)
        if settings_manager is not None and hasattr(settings_manager, 'set_setting'):
            settings_manager.set_setting('volume', int(round(value * 100.0)))
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning('Failed to persist volume setting: %s', exc)

    if not self._is_muted:
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.set_volume(value)
        except MIXER_EXCEPTIONS as exc:
            logger.warning('Failed to set mixer volume: %s', exc)

    _publish_bus_event(
        self,
        AudioEventType.VOLUME_CHANGED,
        {'volume': value},
        context='Failed to publish volume change',
    )


def get_volume(self) -> float:
    """Get the current volume level (0.0 to 1.0)."""
    return float(self._volume)


def set_mute(self, mute: bool) -> None:
    """Set mute state."""
    self._is_muted = bool(mute)
    try:
        pygame.mixer.music.set_volume(0.0 if self._is_muted else self._volume)
    except MIXER_EXCEPTIONS as exc:
        logger.error('Failed to set mute state: %s', exc)

    _publish_bus_event(
        self,
        AudioEventType.MUTE_CHANGED,
        {'muted': self._is_muted},
        context='Failed to publish mute change',
    )


def is_muted(self) -> bool:
    """Check if the audio is muted."""
    return self._is_muted


def toggle_mute(self) -> None:
    """Toggle mute state."""
    self.set_mute(not self._is_muted)


def set_loop(self, loop: bool):
    """Enable or disable looping for subsequent playback without interrupting the current song."""
    self._loop_enabled = bool(loop)


def _on_volume_changed_event(self, data: Dict[str, Any]) -> None:
    """Handle volume change events from the event bus."""
    try:
        self._volume = float(data.get('volume', self._volume))
        if not self._is_muted and pygame.mixer.get_init():
            pygame.mixer.music.set_volume(self._volume)
    except (TypeError, ValueError) as exc:
        logger.error('Failed to parse volume change event: %s', exc)
    except MIXER_EXCEPTIONS as exc:
        logger.error('Failed to handle volume change event: %s', exc)


def _on_settings_batch_updated(self, data: Dict[str, Any]) -> None:
    """Handle batch settings updates from the event bus."""
    del data
    try:
        volume = self.settings_manager.get_setting('volume', int(self._volume * 100))
        self.set_volume(float(volume) / 100.0)
    except (AttributeError, TypeError, ValueError) as exc:
        logger.warning('Unable to apply batch settings: %s', exc)


def process_chunk_offline(self, chunk: 'np.ndarray') -> 'np.ndarray':
    """
    Applica EQ ed effetti a un blocco di dati audio senza riprodurlo.
    Richiede che Equalizer e EffectsEngine siano disponibili.
    """
    processed_chunk = chunk
    if self.equalizer and hasattr(self.equalizer, 'apply_eq_to_chunk'):
        processed_chunk = self.equalizer.apply_eq_to_chunk(processed_chunk)

    if self.effects_engine and hasattr(self.effects_engine, 'apply_effects'):
        processed_chunk = self.effects_engine.apply_effects(processed_chunk)

    return processed_chunk
