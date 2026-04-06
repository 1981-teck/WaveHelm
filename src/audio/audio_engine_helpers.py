from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

import pygame

from src.audio.audio_events import AudioEventType
from src.utils.helpers import get_app_data_path

logger = logging.getLogger(__name__)

SETTINGS_EXCEPTIONS = (AttributeError, KeyError, TypeError, ValueError, OSError)
LOCALIZATION_EXCEPTIONS = (AttributeError, KeyError, TypeError, ValueError)
FORMAT_EXCEPTIONS = (KeyError, IndexError, ValueError)
MIXER_EXCEPTIONS = (pygame.error, AttributeError, RuntimeError, TypeError, ValueError)
DSP_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError, OSError)
EVENT_BUS_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
SOUNDFILE_EXCEPTIONS = (AttributeError, OSError, RuntimeError, ValueError)
CLEANUP_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)


def _get_setting(self, key: str, default: Any) -> Any:
    """Get a setting from the settings manager with fallback."""
    try:
        return self.settings_manager.get_setting(key, default)
    except SETTINGS_EXCEPTIONS as exc:
        logger.warning(
            'Failed to get setting %s: %s, using default: %s', key, exc, default
        )
        return default



def _get_localized_text(self, key: str, **kwargs) -> str:
    """Get localized text with fallback."""
    default = kwargs.pop('default', None)
    try:
        text = self.localization_manager.get_text(key, default or key)
    except LOCALIZATION_EXCEPTIONS as exc:
        logger.warning('Failed to get localized text for key %s: %s', key, exc)
        text = default or key

    try:
        return str(text).format(**kwargs) if kwargs else str(text)
    except FORMAT_EXCEPTIONS:
        return str(text)



def _initialize_mixer(self) -> None:
    """Initialize the pygame mixer with configured settings."""
    try:
        if pygame.mixer.get_init():
            pygame.mixer.quit()
            logger.info('Previous mixer closed for re-init')
        pygame.mixer.init(
            frequency=self.audio_config.frequency,
            size=self.audio_config.size,
            channels=self.audio_config.channels,
            buffer=self.audio_config.buffer,
        )
        pygame.mixer.music.set_volume(0.0 if self._is_muted else self._volume)
        logger.info(
            'Mixer init: %s Hz, size=%s, ch=%s, buffer=%s',
            self.audio_config.frequency,
            self.audio_config.size,
            self.audio_config.channels,
            self.audio_config.buffer,
        )
    except MIXER_EXCEPTIONS as exc:
        logger.critical('Failed to initialize mixer: %s', exc, exc_info=True)
        raise



def bind_dsp_processors(
    self,
    *,
    effects_engine: Optional[EffectsEngine] = None,
    equalizer: Optional[Equalizer] = None,
) -> None:
    """Bind shared DSP processors used by the audio playback pipeline."""
    changed = False

    if effects_engine is not None and effects_engine is not self.effects_engine:
        self.effects_engine = effects_engine
        changed = True

    if equalizer is not None and equalizer is not self.equalizer:
        self.equalizer = equalizer
        changed = True

    if not changed:
        return

    logger.info(
        'Audio DSP processors bound: equalizer=%s effects=%s',
        self.equalizer is not None,
        self.effects_engine is not None,
    )

    if self._current_file and self._is_dsp_processing_active():
        self._schedule_dsp_refresh()



def _sync_dsp_context(self, sample_rate: int, channels: int) -> None:
    """Keep DSP processors aligned with the actual source metadata."""
    if self.equalizer is not None:
        eq_updated = False
        if getattr(self.equalizer, '_sample_rate', None) != sample_rate:
            self.equalizer._sample_rate = int(sample_rate)
            eq_updated = True
        if getattr(self.equalizer, '_channels', None) != channels:
            self.equalizer._channels = int(channels)
            eq_updated = True
        if eq_updated and hasattr(self.equalizer, '_recalculate_all_filters'):
            try:
                self.equalizer._recalculate_all_filters()
            except DSP_EXCEPTIONS:
                logger.debug(
                    'Failed to recalculate equalizer filters for source metadata sync.',
                    exc_info=True,
                )

    if self.effects_engine is not None:
        try:
            self.effects_engine.sample_rate = int(sample_rate)
            self.effects_engine.channels = int(channels)
        except DSP_EXCEPTIONS:
            logger.debug(
                'Failed to sync effects engine context with source metadata.',
                exc_info=True,
            )



def _safe_subscribe(self, event_type: AudioEventType, callback_name: str) -> None:
    callback = getattr(self, callback_name, None)
    if callback is None:
        logger.debug('Skipping subscription for %s: missing callback %s', event_type, callback_name)
        return
    try:
        self.event_bus.subscribe(event_type, callback)
    except EVENT_BUS_EXCEPTIONS as exc:
        logger.error('Failed to subscribe to %s: %s', event_type.name, exc)



def _subscribe_to_events(self) -> None:
    """Subscribe to relevant events from the event bus."""
    _safe_subscribe(self, AudioEventType.VOLUME_CHANGED, '_on_volume_changed_event')
    _safe_subscribe(self, AudioEventType.SETTINGS_BATCH_UPDATED, '_on_settings_batch_updated')
    _safe_subscribe(self, AudioEventType.VIDEO_DURATION_UPDATE, '_on_video_duration_update')
    _safe_subscribe(self, AudioEventType.EQ_CHANGED, '_on_eq_changed')
    _safe_subscribe(self, AudioEventType.EFFECTS_CHANGED, '_on_effects_changed')



def _close_soundfile(self) -> None:
    if self._sf_file:
        try:
            self._sf_file.close()
        except SOUNDFILE_EXCEPTIONS as exc:
            logger.warning('Error closing soundfile: %s', exc)
        finally:
            self._sf_file = None



def _ensure_audio_mixer(self) -> bool:
    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init(
                frequency=self.audio_config.frequency,
                size=self.audio_config.size,
                channels=self.audio_config.channels,
                buffer=self.audio_config.buffer,
            )
            pygame.mixer.music.set_volume(0.0 if self._is_muted else self._volume)
            logger.info('Mixer initialized for audio')
        return True
    except MIXER_EXCEPTIONS as exc:
        logger.critical('Failed to initialize mixer: %s', exc, exc_info=True)
        return False



def _resolve_processed_audio_dir(self) -> Path:
    candidates = [
        get_app_data_path('processed_audio', create=False),
        Path(tempfile.gettempdir()) / 'WaveHelm' / 'processed_audio',
        Path.cwd() / '_wavehelm_processed_audio',
    ]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    return candidates[-1]


def _cleanup_processed_audio_dir(self) -> int:
    directory = getattr(self, '_processed_audio_dir', None)
    if not directory:
        return 0

    try:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
    except CLEANUP_EXCEPTIONS:
        logger.debug('Processed audio directory cleanup skipped.', exc_info=True)
        return 0

    removed = 0
    for item in target.iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=False)
            else:
                item.unlink()
            removed += 1
        except CLEANUP_EXCEPTIONS:
            logger.debug('Unable to remove processed audio artifact %s', item, exc_info=True)

    if removed:
        logger.info('Processed audio cache cleaned on shutdown: %s item(s) removed', removed)
    return removed
