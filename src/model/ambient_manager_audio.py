from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Tuple

import numpy as np

if TYPE_CHECKING:
    import pygame


"""Audio preparation helpers for AmbientManager.

Edge cases handled here:
1. The mixer may be unavailable, partially initialized, or monkeypatched in tests.
   Mitigation: resolve the ambient_manager module state lazily on each call and
   fail deterministically when the mixer cannot be used.
2. Audio files may decode incorrectly or report sample layouts that do not match
   the mixer configuration.
   Mitigation: normalize dtype/channel count, clamp sample data, and raise only
   after the default-speed fallback path has been evaluated.
3. Ambient playback must remain compatible with the active mixer format.
   Mitigation: cache prepared sounds by source path and mixer format only, and
   fall back deterministically when ndarray-based preparation is incompatible.
"""


def _ambient_module():
    from src.model import ambient_manager as ambient_module

    return ambient_module


def _load_raw_audio(self, source_path: Path) -> Tuple[np.ndarray, int]:
    ambient_module = _ambient_module()
    cache_key = str(source_path.resolve()).lower()
    if cache_key in self._raw_cache:
        return self._raw_cache[cache_key]

    audio_data, sample_rate = ambient_module.sf.read(
        str(source_path),
        dtype='float32',
        always_2d=True,
    )
    audio_data = np.asarray(audio_data, dtype=np.float32)
    self._raw_cache[cache_key] = (audio_data, int(sample_rate))
    return self._raw_cache[cache_key]


def _resample_linear(self, audio_data: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    if from_rate == to_rate or audio_data.size == 0:
        return audio_data.astype(np.float32, copy=False)

    ratio = float(to_rate) / float(from_rate)
    new_length = max(1, int(round(audio_data.shape[0] * ratio)))
    old_x = np.arange(audio_data.shape[0], dtype=np.float32)
    new_x = np.linspace(0, audio_data.shape[0] - 1, new_length, dtype=np.float32)

    channels = audio_data.shape[1]
    output = np.empty((new_length, channels), dtype=np.float32)
    for channel_index in range(channels):
        output[:, channel_index] = np.interp(
            new_x,
            old_x,
            audio_data[:, channel_index],
        ).astype(np.float32)
    return output



def _match_mixer_channels(self, audio_data: np.ndarray, target_channels: int) -> np.ndarray:
    if target_channels <= 1:
        if audio_data.shape[1] == 1:
            return audio_data
        return np.mean(audio_data, axis=1, keepdims=True).astype(np.float32)

    if audio_data.shape[1] == target_channels:
        return audio_data
    if audio_data.shape[1] == 1:
        return np.repeat(audio_data, target_channels, axis=1).astype(np.float32)
    return audio_data[:, :target_channels].astype(np.float32)


def _coerce_array_for_mixer(int_data: np.ndarray, target_channels: int) -> np.ndarray:
    normalized = np.asarray(int_data, dtype=np.int16)
    if target_channels <= 1:
        return normalized.reshape(-1)

    if normalized.ndim == 1:
        return np.repeat(normalized[:, None], target_channels, axis=1)
    if normalized.ndim != 2:
        raise ValueError(f'Unsupported ambient array rank: {normalized.ndim}')
    if normalized.shape[1] == target_channels:
        return normalized
    if normalized.shape[1] == 1:
        return np.repeat(normalized, target_channels, axis=1)
    return normalized[:, :target_channels]


def _prepare_sound(self, source_path: Path) -> 'pygame.mixer.Sound':
    ambient_module = _ambient_module()
    pygame_api = ambient_module.pygame
    pygame_exceptions = ambient_module.PYGAME_EXCEPTIONS
    file_exceptions = ambient_module.FILE_EXCEPTIONS

    if not self._ensure_mixer_ready():
        raise RuntimeError('Ambient mixer not available')

    mixer_info = pygame_api.mixer.get_init()
    if not mixer_info:
        raise RuntimeError('Ambient mixer not initialized')
    mixer_rate, _mixer_size, mixer_channels = mixer_info

    cache_key = (
        str(source_path.resolve()).lower(),
        int(mixer_rate),
        int(mixer_channels),
    )
    if cache_key in self._prepared_cache:
        return self._prepared_cache[cache_key]

    try:
        audio_data, sample_rate = self._load_raw_audio(source_path)
        processed = self._resample_linear(audio_data, int(sample_rate), int(mixer_rate))
        processed = self._match_mixer_channels(processed, int(mixer_channels))
        processed = np.clip(processed, -1.0, 1.0)
        int_data = np.asarray(processed * 32767.0, dtype=np.int16)
        mixer_ready_array = _coerce_array_for_mixer(int_data, int(mixer_channels))
        sound = pygame_api.sndarray.make_sound(np.ascontiguousarray(mixer_ready_array))
    except (ValueError, TypeError) + file_exceptions + pygame_exceptions as exc:
        self._logger.debug(
            'Ambient processing fallback to pygame.Sound for %s: %s',
            source_path,
            exc,
            exc_info=True,
        )
        try:
            sound = pygame_api.mixer.Sound(str(source_path))
        except file_exceptions + pygame_exceptions:
            self._logger.debug(
                'Ambient direct pygame.Sound fallback failed for %s',
                source_path,
                exc_info=True,
            )
            raise

    self._prepared_cache[cache_key] = sound
    return sound
