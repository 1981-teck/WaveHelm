from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pygame
import soundfile as sf

from src.audio.audio_events import AudioEventType
from src.audio.audio_engine_shared import VIDEO_EXTS

logger = logging.getLogger(__name__)

EQ_STATE_EXCEPTIONS = (AttributeError, KeyError, RuntimeError, TypeError, ValueError)
EFFECTS_STATE_EXCEPTIONS = (AttributeError, KeyError, RuntimeError, TypeError, ValueError)
DSP_RENDER_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
AUDIO_SOURCE_EXCEPTIONS = (pygame.error, AttributeError, OSError, RuntimeError, TypeError, ValueError)
TIMER_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
PATH_EXCEPTIONS = (AttributeError, OSError, TypeError, ValueError)


def _has_active_equalizer(self) -> bool:
    if not self.equalizer or not getattr(self.equalizer, "is_enabled", False):
        return False
    try:
        gains = self.equalizer.get_current_state().get("band_gains", {})
        return any(abs(float(gain)) > 1e-3 for gain in gains.values())
    except EQ_STATE_EXCEPTIONS:
        return True


def _has_active_effects(self) -> bool:
    if not self.effects_engine or not hasattr(self.effects_engine, "get_current_settings"):
        return False
    try:
        settings = self.effects_engine.get_current_settings()
        return any(
            isinstance(effect_cfg, dict) and bool(effect_cfg.get("enabled"))
            for effect_cfg in settings.values()
        )
    except EFFECTS_STATE_EXCEPTIONS:
        return False


def _is_dsp_processing_active(self) -> bool:
    return self._has_active_equalizer() or self._has_active_effects()


def _build_dsp_cache_key(self, source_path: Path) -> str:
    try:
        stat = source_path.stat()
        source_meta = {
            "path": str(source_path.resolve()),
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
        }
    except OSError:
        source_meta = {"path": str(source_path.resolve())}

    eq_state = {}
    if self.equalizer and hasattr(self.equalizer, "get_current_state"):
        try:
            eq_state = self.equalizer.get_current_state()
        except EQ_STATE_EXCEPTIONS:
            eq_state = {}

    effects_state = {}
    if self.effects_engine and hasattr(self.effects_engine, "get_current_settings"):
        try:
            effects_state = self.effects_engine.get_current_settings()
        except EFFECTS_STATE_EXCEPTIONS:
            effects_state = {}

    payload = {
        "source": source_meta,
        "equalizer": eq_state,
        "effects": effects_state,
    }
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def _render_processed_audio(self, source_path: Path) -> Path:
    cache_key = self._build_dsp_cache_key(source_path)
    output_path = self._processed_audio_dir / f"{source_path.stem}_{cache_key}.wav"
    if output_path.exists():
        return output_path

    audio_data, sample_rate = sf.read(
        str(source_path), dtype="float32", always_2d=True
    )
    processed_data = audio_data
    channels = int(processed_data.shape[1]) if processed_data.ndim > 1 else 1
    self._sync_dsp_context(sample_rate, channels)

    if self.equalizer and self._has_active_equalizer():
        processed_data = self.equalizer.apply_eq_to_chunk(processed_data)

    if self.effects_engine and self._has_active_effects():
        processed_data = self.effects_engine.apply_effects(processed_data)

    if processed_data.size:
        peak = float(np.max(np.abs(processed_data)))
        if peak > 1.0:
            processed_data = processed_data / peak
        processed_data = np.clip(processed_data, -1.0, 1.0)

    sf.write(str(output_path), processed_data.astype(np.float32), sample_rate, subtype="PCM_16")
    logger.info("Processed audio rendered for DSP playback: %s", output_path)
    return output_path


def _resolve_playback_source(self, source_path: Path) -> Path:
    if not self._is_dsp_processing_active():
        return source_path
    try:
        return self._render_processed_audio(source_path)
    except DSP_RENDER_EXCEPTIONS as error:
        logger.error(
            "Failed to render DSP playback source for %s: %s",
            source_path,
            error,
            exc_info=True,
        )
        return source_path


def _load_audio_source(self, source_path: Path, playback_path: Optional[Path] = None) -> bool:
    playback_path = playback_path or self._resolve_playback_source(source_path)
    self._close_soundfile()

    try:
        pygame.mixer.music.load(str(playback_path))
        pygame.mixer.music.set_volume(0.0 if self._is_muted else self._volume)
        self._is_music_loaded = True
        self._playback_source_file = str(playback_path)
        logger.info(
            "Audio source loaded into mixer: source=%s playback=%s",
            source_path,
            playback_path,
        )
        self._sf_file = sf.SoundFile(str(playback_path), "r")
        return True
    except AUDIO_SOURCE_EXCEPTIONS as error:
        logger.critical(
            "Mixer or SoundFile cannot load file: %s -> %s - %s",
            source_path,
            playback_path,
            error,
            exc_info=True,
        )
        self._is_music_loaded = False
        self._playback_source_file = None
        self._close_soundfile()
        return False


def _cancel_dsp_refresh_timer(self) -> None:
    if self._dsp_refresh_timer is not None:
        try:
            self._dsp_refresh_timer.cancel()
        except TIMER_EXCEPTIONS as error:
            logger.debug(
                "Failed to cancel DSP refresh timer cleanly: %s",
                error,
                exc_info=True,
            )
        self._dsp_refresh_timer = None


def _schedule_dsp_refresh(self) -> None:
    current_file = self._current_file
    if not current_file:
        return
    try:
        if Path(current_file).suffix.lower() in VIDEO_EXTS:
            return
    except PATH_EXCEPTIONS:
        return

    self._cancel_dsp_refresh_timer()
    timer = threading.Timer(self._dsp_refresh_delay, self._refresh_dsp_playback_source)
    timer.daemon = True
    self._dsp_refresh_timer = timer
    timer.start()


def _paths_match(current_path: Optional[str], target_path: Path) -> bool:
    if not current_path:
        return False
    try:
        return Path(current_path).resolve() == target_path.resolve()
    except OSError:
        return str(Path(current_path)) == str(target_path)


def _refresh_dsp_playback_source(self) -> None:
    with self._dsp_render_lock:
        self._dsp_refresh_timer = None
        source_file = self._current_file
        if not source_file:
            return

        source_path = Path(source_file)
        if source_path.suffix.lower() in VIDEO_EXTS:
            return

        target_playback_path = self._resolve_playback_source(source_path)

        if self._current_file != str(source_path):
            logger.debug('Skipping DSP refresh because current file changed during pre-render.')
            return

        if _paths_match(getattr(self, '_playback_source_file', None), target_playback_path):
            logger.debug('Skipping DSP playback refresh; target source unchanged: %s', target_playback_path)
            return

        was_playing = self.is_playing()
        was_paused = self.is_paused()
        current_position = self.get_position()

        try:
            self._stop_progress_loop()
            try:
                pygame.mixer.music.stop()
            except AUDIO_SOURCE_EXCEPTIONS as error:
                logger.debug(
                    "Mixer stop failed during DSP refresh restart: %s",
                    error,
                    exc_info=True,
                )

            if not self._ensure_audio_mixer():
                return

            if self._current_file != str(source_path):
                logger.debug('Skipping DSP reload because current file changed after stop.')
                return

            if not self._load_audio_source(source_path, playback_path=target_playback_path):
                return

            if self._current_file != str(source_path):
                logger.debug('Skipping DSP restart because current file changed after reload.')
                return

            self._current_position = max(0.0, min(current_position, self._total_length or current_position))
            self._seek_base = self._current_position

            if was_playing:
                play_loops = -1 if bool(getattr(self, '_loop_enabled', False)) else 0
                pygame.mixer.music.play(loops=play_loops, start=self._current_position)
                self._current_play_uses_native_loop = bool(play_loops == -1)
                self._is_paused = False
                self._start_progress_loop()
            elif was_paused:
                self._is_paused = True
            else:
                self._is_paused = False

            logger.info(
                "Audio playback source refreshed after DSP change: %s",
                self._playback_source_file or source_path,
            )
        except AUDIO_SOURCE_EXCEPTIONS as error:
            logger.error(
                "Failed to refresh playback source after DSP change: %s",
                error,
                exc_info=True,
            )


def _on_eq_changed(self, _data: Dict[str, Any]) -> None:
    self._schedule_dsp_refresh()


def _on_effects_changed(self, _data: Dict[str, Any]) -> None:
    self._schedule_dsp_refresh()
