# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
from math import ceil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf

try:
    import pygame
except ImportError:  # pragma: no cover
    pygame = None  # type: ignore

from src.audio.audio_events import AudioEventType
from src.model.ambient_manager_audio import (
    _load_raw_audio,
    _match_mixer_channels,
    _prepare_sound,
    _resample_linear,
)
from src.utils.helpers import get_app_data_path, is_audio_file

logger = logging.getLogger(__name__)

LOCALIZATION_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
SETTINGS_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
EVENT_BUS_EXCEPTIONS = (AttributeError, RuntimeError, TypeError)
FILE_EXCEPTIONS = (OSError, RuntimeError, TypeError, ValueError)
PATH_EXCEPTIONS = (OSError, RuntimeError, ValueError)

if pygame is not None and hasattr(pygame, 'error'):
    PYGAME_EXCEPTIONS = (AttributeError, pygame.error)
else:  # pragma: no cover
    PYGAME_EXCEPTIONS = (AttributeError, RuntimeError)


_AMBIENT_SOUND_LOCALIZATION_FALLBACKS: Dict[str, Dict[str, str]] = {
    'birds': {'en': 'Birds', 'it': 'Uccellini', 'es': 'Pajaritos', 'fr': 'Oiseaux'},
    'cascata': {'en': 'Waterfall', 'it': 'Cascata', 'es': 'Cascada', 'fr': 'Cascade'},
    'dripping_water': {'en': 'Dripping Water', 'it': 'Goccia che cade', 'es': 'Gota de agua', 'fr': "Goutte d'eau"},
    'foresta': {'en': 'Forest', 'it': 'Foresta', 'es': 'Bosque', 'fr': 'Forêt'},
    'forest': {'en': 'Forest', 'it': 'Foresta', 'es': 'Bosque', 'fr': 'Forêt'},
    'goccia_che_cade': {'en': 'Dripping Water', 'it': 'Goccia che cade', 'es': 'Gota de agua', 'fr': "Goutte d'eau"},
    'mare': {'en': 'Sea', 'it': 'Mare', 'es': 'Mar', 'fr': 'Mer'},
    'pioggia': {'en': 'Rain', 'it': 'Pioggia', 'es': 'Lluvia', 'fr': 'Pluie'},
    'rain': {'en': 'Rain', 'it': 'Pioggia', 'es': 'Lluvia', 'fr': 'Pluie'},
    'sea': {'en': 'Sea', 'it': 'Mare', 'es': 'Mar', 'fr': 'Mer'},
    'storm': {'en': 'Thunderstorm', 'it': 'Temporale', 'es': 'Tormenta', 'fr': 'Orage'},
    'temporale': {'en': 'Thunderstorm', 'it': 'Temporale', 'es': 'Tormenta', 'fr': 'Orage'},
    'traffic': {'en': 'Traffic', 'it': 'Traffico', 'es': 'Tráfico', 'fr': 'Trafic'},
    'traffico': {'en': 'Traffic', 'it': 'Traffico', 'es': 'Tráfico', 'fr': 'Trafic'},
    'uccellini': {'en': 'Birds', 'it': 'Uccellini', 'es': 'Pajaritos', 'fr': 'Oiseaux'},
    'vento': {'en': 'Wind', 'it': 'Vento', 'es': 'Viento', 'fr': 'Vent'},
    'waterfall': {'en': 'Waterfall', 'it': 'Cascata', 'es': 'Cascada', 'fr': 'Cascade'},
    'wind': {'en': 'Wind', 'it': 'Vento', 'es': 'Viento', 'fr': 'Vent'},
}


class AmbientManager:
    """Ambient background player with independent volume."""

    def __init__(
        self,
        settings_manager=None,
        event_bus=None,
        localization_manager=None,
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self.settings_manager = settings_manager
        self.event_bus = event_bus
        self.localization_manager = localization_manager

        self._channel: Optional['pygame.mixer.Channel'] = None
        self._sound: Optional['pygame.mixer.Sound'] = None
        self._source_path: Optional[str] = None
        self._current_name: Optional[str] = None
        self._current_sound_id: Optional[str] = None

        self._volume: float = self._read_setting('ambient_volume', 0.4)
        self._muted: bool = bool(self._read_setting('ambient_muted', False))
        self._enabled: bool = False

        self._sound_map: Dict[str, Path] = {}
        self._sound_display_to_id: Dict[str, str] = {}
        self._path_display_map: Dict[str, str] = {}
        self._path_sound_id_map: Dict[str, str] = {}
        self._raw_cache: Dict[str, Tuple[np.ndarray, int]] = {}
        self._prepared_cache: Dict[Tuple[str, int, int, float], 'pygame.mixer.Sound'] = {}

        self._ambient_dirs = self._build_search_dirs()
        self._register_language_callback()
        self.refresh_ambient_library()
        self._ensure_mixer_ready()

        self._logger.info(self._t('ambient_manager_initialized', 'AmbientManager initialized.'))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _t(self, key: str, default: str, **kwargs: Any) -> str:
        text = default
        try:
            if self.localization_manager is not None:
                text = str(self.localization_manager.get_text(key, default))
        except LOCALIZATION_EXCEPTIONS:
            text = default
        try:
            return text.format(**kwargs) if kwargs else text
        except (KeyError, IndexError, ValueError):
            return text

    def _read_setting(self, key: str, default: Any) -> Any:
        try:
            if self.settings_manager is not None:
                return self.settings_manager.get_setting(key, default)
        except SETTINGS_EXCEPTIONS as error:
            self._logger.debug(
                'Failed to read ambient setting %s: %s',
                key,
                error,
                exc_info=True,
            )
        return default

    def _write_setting(self, key: str, value: Any) -> None:
        try:
            if self.settings_manager is not None:
                self.settings_manager.set_setting(key, value)
        except SETTINGS_EXCEPTIONS:
            self._logger.debug('Failed to persist ambient setting %s', key, exc_info=True)

    def _publish(self, event_type: AudioEventType, payload: Optional[Dict[str, Any]] = None) -> None:
        try:
            if self.event_bus is not None:
                self.event_bus.publish(event_type, payload or {})
        except EVENT_BUS_EXCEPTIONS:
            self._logger.debug('Ambient event publish failed: %s', event_type, exc_info=True)

    def _register_language_callback(self) -> None:
        register_callback = getattr(self.localization_manager, 'register_language_change_callback', None)
        if not callable(register_callback):
            return
        try:
            register_callback(self._handle_language_change)
        except LOCALIZATION_EXCEPTIONS:
            self._logger.debug('Ambient language callback registration failed.', exc_info=True)

    def _handle_language_change(self, *_: Any) -> None:
        """Refresh localized sound labels when the active UI language changes.

        Edge cases considered:
        - The callback may fire before the ambient library is populated.
        - Custom files may not have a localized label and must keep a deterministic fallback.
        - Active playback must keep resolving the same source path after a language switch.
        """
        self.refresh_ambient_library()
        if self._source_path:
            self._current_name = self.get_current_sound_name()

    def _get_current_language(self) -> str:
        getter = getattr(self.localization_manager, 'get_current_language', None)
        if callable(getter):
            try:
                language = str(getter()).strip().lower()
                if language:
                    return language
            except LOCALIZATION_EXCEPTIONS:
                self._logger.debug('Ambient language resolution failed.', exc_info=True)
        return 'en'

    @staticmethod
    def _sanitize_sound_id_segment(value: str) -> str:
        normalized = re.sub(r'[^a-z0-9]+', '_', str(value).strip().lower())
        return normalized.strip('_') or 'ambient_sound'

    def _build_sound_id(self, base_dir: Path, file_path: Path) -> str:
        """Build a stable sound identifier independent from the current UI language.

        Edge cases considered:
        - Files outside the expected base folder must still yield a deterministic identifier.
        - Nested folders must not collide with root-level files that share the same stem.
        - Spaces and punctuation in user-provided filenames must be normalized safely.
        """
        try:
            relative = file_path.relative_to(base_dir)
        except ValueError:
            relative = Path(file_path.name)

        parts = [self._sanitize_sound_id_segment(part) for part in relative.parts[:-1] if part]
        stem = self._sanitize_sound_id_segment(relative.stem)
        return '__'.join([*parts, stem]) if parts else stem

    def _localize_builtin_sound_name(self, sound_id: str) -> Optional[str]:
        language = self._get_current_language()
        candidates = [sound_id]
        if '__' in sound_id:
            candidates.append(sound_id.rsplit('__', 1)[-1])
        for candidate in candidates:
            labels = _AMBIENT_SOUND_LOCALIZATION_FALLBACKS.get(candidate)
            if not labels:
                continue
            return labels.get(language) or labels.get('en')
        return None

    def _display_name_for_path(self, base_dir: Path, file_path: Path, sound_id: Optional[str] = None) -> str:
        fallback_name = self._friendly_sound_name(base_dir, file_path)
        resolved_sound_id = sound_id or self._build_sound_id(base_dir, file_path)
        localized_name = self._localize_builtin_sound_name(resolved_sound_id)
        return localized_name or fallback_name

    def _build_search_dirs(self) -> List[Path]:
        user_dir = get_app_data_path('ambient_sounds', create=False)
        project_root = Path(__file__).resolve().parents[2]
        fallback_dir = project_root / 'ambient_sounds'
        candidates = [
            user_dir,
            fallback_dir,
            project_root / 'src' / 'resources' / 'ambient_sounds',
        ]

        unique: List[Path] = []
        seen = set()
        for candidate in candidates:
            resolved = candidate.resolve()
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            if candidate == user_dir:
                try:
                    candidate.mkdir(parents=True, exist_ok=True)
                except OSError:
                    self._logger.debug(
                        'Ambient user directory not writable, fallback will be used: %s',
                        candidate,
                        exc_info=True,
                    )
                    continue
            elif candidate == fallback_dir:
                try:
                    candidate.mkdir(parents=True, exist_ok=True)
                except OSError as error:
                    self._logger.debug(
                        'Ambient fallback directory not writable: %s (%s)',
                        candidate,
                        error,
                        exc_info=True,
                    )
            unique.append(candidate)

        if not unique:
            fallback_dir.mkdir(parents=True, exist_ok=True)
            unique.append(fallback_dir)
        return unique

    def get_primary_ambient_dir(self) -> Path:
        return self._ambient_dirs[0]

    def get_search_dirs(self) -> List[Path]:
        return list(self._ambient_dirs)

    def get_mix_export_dir(self) -> Path:
        return get_app_data_path('ambient mix saved')

    def _ensure_mixer_ready(self) -> bool:
        if pygame is None or not hasattr(pygame, 'mixer'):
            self._logger.warning('pygame.mixer not available; ambient disabled.')
            return False

        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2)

            try:
                num_channels = pygame.mixer.get_num_channels()
            except PYGAME_EXCEPTIONS:
                num_channels = 8

            if num_channels < 8:
                try:
                    pygame.mixer.set_num_channels(8)
                    num_channels = 8
                except PYGAME_EXCEPTIONS:
                    num_channels = max(1, num_channels)

            channel_index = max(0, num_channels - 1)
            self._channel = pygame.mixer.Channel(channel_index)
            return True
        except PYGAME_EXCEPTIONS as exc:
            self._logger.error('Ambient mixer init failed: %s', exc, exc_info=True)
            return False

    def _effective_volume(self) -> float:
        return 0.0 if self._muted else float(self._volume)

    def _friendly_sound_name(self, base_dir: Path, file_path: Path) -> str:
        try:
            relative = file_path.relative_to(base_dir)
        except ValueError:
            relative = Path(file_path.name)

        rel_path = Path(relative)
        stem = rel_path.stem.replace('_', ' ').replace('-', ' ').strip()
        display_stem = stem or rel_path.stem
        if rel_path.parent == Path('.'):
            return display_stem
        parent = str(rel_path.parent).replace('\\', ' / ').replace('/', ' / ')
        return f'{parent} / {display_stem}'

    def refresh_ambient_library(self) -> None:
        sound_map: Dict[str, Path] = {}
        sound_display_to_id: Dict[str, str] = {}
        path_display_map: Dict[str, str] = {}
        path_sound_id_map: Dict[str, str] = {}
        duplicate_count: Dict[str, int] = {}

        for base_dir in self._ambient_dirs:
            if not base_dir.exists():
                continue
            for file_path in sorted(base_dir.rglob('*')):
                if not file_path.is_file():
                    continue
                if not is_audio_file(str(file_path)):
                    continue

                sound_id = self._build_sound_id(base_dir, file_path)
                display_name = self._display_name_for_path(base_dir, file_path, sound_id)
                key = display_name
                if key in sound_map:
                    duplicate_count[key] = duplicate_count.get(key, 1) + 1
                    key = f'{display_name} ({duplicate_count[display_name]})'
                path_key = str(file_path.resolve()).lower()
                sound_map[key] = file_path
                sound_display_to_id[key] = sound_id
                path_display_map[path_key] = key
                path_sound_id_map[path_key] = sound_id

        self._sound_map = dict(sorted(sound_map.items(), key=lambda item: item[0].lower()))
        self._sound_display_to_id = dict(sound_display_to_id)
        self._path_display_map = dict(path_display_map)
        self._path_sound_id_map = dict(path_sound_id_map)

    def get_available_ambient_sounds(self) -> List[str]:
        self.refresh_ambient_library()
        return list(self._sound_map.keys())

    def _resolve_sound_path(self, sound_name_or_path: Optional[str]) -> Optional[Path]:
        if not sound_name_or_path:
            return None
        direct = Path(sound_name_or_path)
        if direct.exists() and direct.is_file():
            return direct
        if sound_name_or_path in self._sound_map:
            return self._sound_map[sound_name_or_path]
        self.refresh_ambient_library()
        return self._sound_map.get(sound_name_or_path)

    def _restart_if_playing(self) -> None:
        if not self._enabled or not self._source_path:
            return
        current = self._current_name or self._source_path
        self.play_ambient_sound(current)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_current_sound_name(self) -> Optional[str]:
        if not self._source_path:
            return self._current_name
        path_key = str(Path(self._source_path).resolve()).lower()
        if path_key in self._path_display_map:
            return self._path_display_map[path_key]
        source_path = Path(self._source_path)
        sound_id = self._current_sound_id or self._path_sound_id_map.get(path_key)
        return self._display_name_for_path(source_path.parent, source_path, sound_id)

    def get_current_source(self) -> Optional[str]:
        return self._source_path

    def get_ambient_volume(self) -> float:
        return float(self._volume)

    def is_ambient_muted(self) -> bool:
        return bool(self._muted)

    def load(self, sound_name_or_path: str) -> bool:
        source_path = self._resolve_sound_path(sound_name_or_path)
        if source_path is None:
            return False
        try:
            self._sound = self._prepare_sound(source_path)
            self._source_path = str(source_path)
            path_key = str(source_path.resolve()).lower()
            self._current_sound_id = self._path_sound_id_map.get(path_key)
            if self._current_sound_id is None:
                self._current_sound_id = self._build_sound_id(source_path.parent, source_path)
            self._current_name = self.get_current_sound_name()
            return True
        except (FILE_EXCEPTIONS + PYGAME_EXCEPTIONS) as exc:
            self._logger.error(
                self._t(
                    'ambient_error_playing_sound',
                    "Error playing ambient sound '{file}': {error}",
                    file=source_path.name,
                    error=exc,
                ),
                exc_info=True,
            )
            self._sound = None
            return False

    def start(self) -> bool:
        if not self._sound and self._source_path:
            if not self.load(self._source_path):
                return False
        if not self._sound or not self._ensure_mixer_ready():
            return False
        try:
            assert self._channel is not None
            self._channel.stop()
            self._channel.play(self._sound, loops=-1)
            self._channel.set_volume(self._effective_volume())
            self._enabled = True
            self._publish(
                AudioEventType.AMBIENT_STARTED,
                {
                    'source': self._source_path,
                    'name': self._current_name,
                    'volume': self._volume,
                },
            )
            return True
        except (AssertionError,) + PYGAME_EXCEPTIONS as exc:
            self._logger.error('Ambient start failed: %s', exc, exc_info=True)
            self._enabled = False
            return False

    def play_ambient_sound(self, sound_name_or_path: Optional[str] = None) -> bool:
        if sound_name_or_path is not None and not self.load(sound_name_or_path):
            return False
        return self.start()

    def stop(self) -> None:
        self.stop_ambient_sound()

    def stop_ambient_sound(self) -> None:
        mixer_ready = False
        try:
            mixer_ready = bool(
                pygame is not None
                and hasattr(pygame, 'mixer')
                and hasattr(pygame.mixer, 'get_init')
                and pygame.mixer.get_init()
            )
        except PYGAME_EXCEPTIONS:
            mixer_ready = False

        try:
            if mixer_ready and self._channel is not None:
                self._channel.stop()
        except PYGAME_EXCEPTIONS:
            self._logger.debug('Ambient stop error', exc_info=True)
        finally:
            self._enabled = False
            self._publish(
                AudioEventType.AMBIENT_STOPPED,
                {'source': self._source_path, 'name': self._current_name},
            )

    def toggle(self) -> bool:
        if self.is_playing():
            self.stop_ambient_sound()
            return False
        return self.start()

    def set_volume(self, vol: float) -> None:
        self.set_ambient_volume(vol)

    def set_ambient_volume(self, vol: float) -> None:
        self._volume = max(0.0, min(1.0, float(vol)))
        self._write_setting('ambient_volume', self._volume)
        try:
            if self._channel is not None:
                self._channel.set_volume(self._effective_volume())
        except PYGAME_EXCEPTIONS:
            self._logger.debug('Ambient set_volume error', exc_info=True)
        self._publish(AudioEventType.AMBIENT_VOLUME, {'volume': self._volume})


    def set_ambient_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self._write_setting('ambient_muted', self._muted)
        try:
            if self._channel is not None:
                self._channel.set_volume(self._effective_volume())
        except PYGAME_EXCEPTIONS:
            self._logger.debug('Ambient mute update failed', exc_info=True)
        self._publish(AudioEventType.AMBIENT_MUTED_CHANGED, {'muted': self._muted})

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------
    def is_playing(self) -> bool:
        try:
            return bool(self._channel and self._channel.get_busy())
        except PYGAME_EXCEPTIONS:
            return False

    def current_source(self) -> Optional[str]:
        return self._source_path

    def _read_mix_audio_file(self, source_path: Path) -> Tuple[np.ndarray, int]:
        audio_data, sample_rate = sf.read(str(source_path), dtype='float32', always_2d=True)
        normalized = np.asarray(audio_data, dtype=np.float32)
        if normalized.ndim != 2:
            raise ValueError(f'Unsupported audio rank for mix export: {normalized.ndim}')
        if normalized.shape[0] == 0:
            raise ValueError(f'Audio source is empty: {source_path}')
        return normalized, int(sample_rate)

    @staticmethod
    def _loop_or_trim_audio(audio_data: np.ndarray, target_frames: int) -> np.ndarray:
        if target_frames <= 0:
            raise ValueError('Target frame count must be positive for mix export.')
        if audio_data.shape[0] == 0:
            return np.zeros((target_frames, audio_data.shape[1]), dtype=np.float32)
        if audio_data.shape[0] >= target_frames:
            return np.asarray(audio_data[:target_frames], dtype=np.float32)
        repeat_count = int(ceil(float(target_frames) / float(audio_data.shape[0])))
        tiled = np.tile(audio_data, (repeat_count, 1))
        return np.asarray(tiled[:target_frames], dtype=np.float32)

    def export_audio_mix(
        self,
        *,
        main_source_path: str,
        output_path: str,
        ambient_sound_name_or_path: Optional[str] = None,
        main_volume: float = 1.0,
        main_muted: bool = False,
    ) -> Path:
        main_path = Path(main_source_path)
        if not main_path.exists() or not main_path.is_file():
            raise FileNotFoundError(f'Main source file not found: {main_path}')

        ambient_reference = ambient_sound_name_or_path or self._source_path or self._current_name
        ambient_path = self._resolve_sound_path(ambient_reference)
        if ambient_path is None or not ambient_path.exists() or not ambient_path.is_file():
            raise FileNotFoundError('Ambient source file not found for mix export.')

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        main_audio, main_rate = self._read_mix_audio_file(main_path)
        ambient_audio, ambient_rate = self._read_mix_audio_file(ambient_path)
        ambient_audio = self._resample_linear(ambient_audio, int(ambient_rate), int(main_rate))
        ambient_audio = self._match_mixer_channels(ambient_audio, int(main_audio.shape[1]))
        ambient_audio = self._loop_or_trim_audio(ambient_audio, int(main_audio.shape[0]))

        safe_main_volume = 0.0 if main_muted else max(0.0, min(1.0, float(main_volume)))
        safe_ambient_volume = 0.0 if self._muted else max(0.0, min(1.0, float(self._volume)))

        mixed_audio = (main_audio * safe_main_volume) + (ambient_audio * safe_ambient_volume)
        mixed_audio = np.clip(mixed_audio, -1.0, 1.0).astype(np.float32, copy=False)

        sf.write(str(output), mixed_audio, int(main_rate))
        return output

    def close(self) -> None:
        unregister_callback = getattr(self.localization_manager, 'unregister_language_change_callback', None)
        if callable(unregister_callback):
            try:
                unregister_callback(self._handle_language_change)
            except LOCALIZATION_EXCEPTIONS:
                self._logger.debug('Ambient language callback unregister failed.', exc_info=True)
        self.stop_ambient_sound()
        self._prepared_cache.clear()
        self._raw_cache.clear()
        self._logger.info(self._t('ambient_manager_closed', 'Ambient Manager closed.'))


_AMBIENT_MANAGER_AUDIO_METHODS: tuple[tuple[str, Any], ...] = (
    ("_load_raw_audio", _load_raw_audio),
    ("_resample_linear", _resample_linear),
    ("_match_mixer_channels", _match_mixer_channels),
    ("_prepare_sound", _prepare_sound),
)


def install_ambient_manager_audio_behavior(
    manager_cls: type[AmbientManager],
) -> type[AmbientManager]:
    """Install audio-processing helpers while keeping this module as the public entrypoint.

    Edge cases considered:
    - Tests monkeypatch historical module symbols such as ``pygame`` and ``PYGAME_EXCEPTIONS``.
    - Audio preparation depends on mixer format and source decoding.
    - Re-import or reload should not repeat helper attachment without an idempotent guard.
    """
    if not isinstance(manager_cls, type):
        raise TypeError("Ambient manager audio target must be a class")
    if getattr(manager_cls, '_ambient_manager_audio_behavior_attached', False):
        return manager_cls

    seen_names: set[str] = set()
    for attribute_name, method in _AMBIENT_MANAGER_AUDIO_METHODS:
        if not attribute_name:
            raise TypeError('Ambient manager binding name cannot be empty')
        if attribute_name in seen_names:
            raise TypeError(f'Duplicate ambient manager binding: {attribute_name}')
        if not callable(method):
            raise TypeError(f'Invalid ambient manager binding: {attribute_name}')
        setattr(manager_cls, attribute_name, method)
        seen_names.add(attribute_name)

    setattr(manager_cls, '_ambient_manager_audio_behavior_attached', True)
    return manager_cls


_AMBIENT_MANAGER_ATTACHERS: tuple[tuple[str, Any], ...] = (
    ("audio", install_ambient_manager_audio_behavior),
)


def install_ambient_manager_behavior(
    manager_cls: type[AmbientManager],
) -> type[AmbientManager]:
    if not isinstance(manager_cls, type):
        raise TypeError("Ambient manager target must be a class")
    if getattr(manager_cls, '_ambient_manager_behavior_attached', False):
        return manager_cls

    for group_name, installer in _AMBIENT_MANAGER_ATTACHERS:
        if not callable(installer):
            raise TypeError(f'Invalid ambient manager installer: {group_name}')
        installer(manager_cls)

    setattr(manager_cls, '_ambient_manager_behavior_attached', True)
    return manager_cls


def attach_ambient_manager_audio_behavior(
    manager_cls: type[AmbientManager],
) -> type[AmbientManager]:
    """Backward-compatible shim that delegates to the neutral audio installer."""
    return install_ambient_manager_audio_behavior(manager_cls)


def attach_ambient_manager_behavior(
    manager_cls: type[AmbientManager],
) -> type[AmbientManager]:
    """Backward-compatible shim that delegates to the neutral installer."""
    return install_ambient_manager_behavior(manager_cls)


install_ambient_manager_behavior(AmbientManager)
