"""AudioEngine - cleaned build: reduced log noise, utility getters, safe guards."""

from __future__ import annotations

import logging
import threading
from typing import Optional

import soundfile as sf

from .audio_config import get_audio_config
from src.model.localization_manager import LocalizationManager
from src.model.setting_manager import SettingsManager
from src.audio.audio_events import AudioEventBus
from src.audio.effects import EffectsEngine
from src.audio.equalizer import Equalizer
from src.audio.analysis import AudioAnalyzer
from src.audio.audio_engine_shared import VIDEO_EXTS
from src.audio import audio_engine_helpers as helpers
from src.audio import audio_engine_dsp as dsp
from src.audio import audio_engine_playback as playback
from src.audio import audio_engine_progress as progress

logger = logging.getLogger(__name__)


class AudioEngine:
    """
    Audio engine based on pygame.mixer.music.
    - File loading (+ duration via soundfile, with shared audio metadata fallback)
    - Play/pause/stop/seek (absolute time)
    - Volume/mute
    - Events: PLAYBACK_PROGRESS / PLAYBACK_ENDED / MEDIA_DURATION_UPDATE
    - Compatibility: is_loaded, set_source, play_file, get_length/get_duration
    """

    @property
    def current_file(self) -> "Optional[str]":
        """Path of the currently loaded file, or None."""
        return getattr(self, "_current_file", None)

    @property
    def playback_source_file(self) -> "Optional[str]":
        """Path of the active audio render source used by the mixer."""
        return getattr(self, "_playback_source_file", None) or getattr(self, "_current_file", None)

    @property
    def is_loaded(self) -> bool:
        """Check if a file is currently loaded."""
        return bool(self._current_file)

    def set_source(self, file_path: str) -> bool:
        """Compatibility method: set the current file."""
        return self.set_file(file_path)

    def play_file(self, file_path: str) -> None:
        """Compatibility method: set file and play."""
        if self._current_file == file_path and self._is_paused:
            self.resume()
        elif self.set_file(file_path):
            self.play()

    def __init__(
        self,
        localization_manager: LocalizationManager,
        settings_manager: SettingsManager,
        event_bus: AudioEventBus,
        effects_engine: Optional[EffectsEngine] = None,
        equalizer: Optional[Equalizer] = None,
    ) -> None:
        self.localization_manager = localization_manager
        self.settings_manager = settings_manager
        self.event_bus = event_bus
        self.effects_engine = effects_engine
        self.equalizer = equalizer

        self.audio_config = get_audio_config()

        self._current_file: Optional[str] = None
        self._playback_source_file: Optional[str] = None
        self._total_length: float = 0.0
        self._current_position: float = 0.0
        self._seek_base: float = 0.0
        self._is_muted: bool = False
        self._is_paused: bool = False
        init_volume = float(self._get_setting("volume", 70)) / 100.0
        self._volume: float = max(0.0, min(1.0, init_volume))
        self._loop_enabled: bool = bool(self._get_setting("loop", False))
        self._current_play_uses_native_loop: bool = False

        self.audio_analyzer = AudioAnalyzer(
            fft_size=4096, sample_rate=self.audio_config.frequency, num_bands=32
        )
        self._sf_file: Optional[sf.SoundFile] = None
        self._dsp_render_lock = threading.RLock()
        self._dsp_refresh_timer = None
        self._dsp_refresh_delay: float = 0.18
        self._processed_audio_dir = self._resolve_processed_audio_dir()

        self._progress_thread = None
        self._progress_stop_event = threading.Event()
        self._was_playing: bool = False
        self._progress_publish_interval: float = 0.10
        self._spectrum_publish_interval: float = 0.04
        self._poll_sleep_interval: float = 0.02

        self._initialize_mixer()
        self._subscribe_to_events()

        logger.info(
            "AudioEngine initialized (vol=%.2f mute=%s)", self._volume, self._is_muted
        )

AudioEngine._get_setting = helpers._get_setting
AudioEngine._get_localized_text = helpers._get_localized_text
AudioEngine._initialize_mixer = helpers._initialize_mixer
AudioEngine.bind_dsp_processors = helpers.bind_dsp_processors
AudioEngine._sync_dsp_context = helpers._sync_dsp_context
AudioEngine._subscribe_to_events = helpers._subscribe_to_events
AudioEngine._close_soundfile = helpers._close_soundfile
AudioEngine._ensure_audio_mixer = helpers._ensure_audio_mixer
AudioEngine._resolve_processed_audio_dir = helpers._resolve_processed_audio_dir
AudioEngine._cleanup_processed_audio_dir = helpers._cleanup_processed_audio_dir
AudioEngine._has_active_equalizer = dsp._has_active_equalizer
AudioEngine._has_active_effects = dsp._has_active_effects
AudioEngine._is_dsp_processing_active = dsp._is_dsp_processing_active
AudioEngine._build_dsp_cache_key = dsp._build_dsp_cache_key
AudioEngine._render_processed_audio = dsp._render_processed_audio
AudioEngine._resolve_playback_source = dsp._resolve_playback_source
AudioEngine._load_audio_source = dsp._load_audio_source
AudioEngine._cancel_dsp_refresh_timer = dsp._cancel_dsp_refresh_timer
AudioEngine._schedule_dsp_refresh = dsp._schedule_dsp_refresh
AudioEngine._refresh_dsp_playback_source = dsp._refresh_dsp_playback_source
AudioEngine._on_eq_changed = dsp._on_eq_changed
AudioEngine._on_effects_changed = dsp._on_effects_changed
AudioEngine.set_file = playback.set_file
AudioEngine._on_video_duration_update = playback._on_video_duration_update
AudioEngine.close = playback.close
AudioEngine.play = playback.play
AudioEngine.pause = playback.pause
AudioEngine.resume = playback.resume
AudioEngine.stop = playback.stop
AudioEngine.is_playing = playback.is_playing
AudioEngine.is_paused = playback.is_paused
AudioEngine.get_length = playback.get_length
AudioEngine.get_duration = playback.get_duration
AudioEngine.get_position = playback.get_position
AudioEngine.seek = playback.seek
AudioEngine._start_progress_loop = progress._start_progress_loop
AudioEngine._stop_progress_loop = progress._stop_progress_loop
AudioEngine.set_volume = progress.set_volume
AudioEngine.get_volume = progress.get_volume
AudioEngine.set_mute = progress.set_mute
AudioEngine.is_muted = progress.is_muted
AudioEngine.toggle_mute = progress.toggle_mute
AudioEngine.set_loop = progress.set_loop
AudioEngine._on_volume_changed_event = progress._on_volume_changed_event
AudioEngine._on_settings_batch_updated = progress._on_settings_batch_updated
AudioEngine.process_chunk_offline = progress.process_chunk_offline

__all__ = ["AudioEngine"]
