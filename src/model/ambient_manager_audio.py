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
    """Preserve channels by position; missing non-mono channels contain silence.

    Invalid ranks/empty channel axes are rejected. Mono duplication and the legacy
    mean-to-mono/leading-channel reduction remain unchanged. Expansion is padding,
    not a speaker-layout-aware surround remix; it also serves file mix export.
    """
    if target_channels < 1:
        raise ValueError('Ambient target channel count must be positive')
    if audio_data.ndim != 2 or audio_data.shape[1] < 1:
        raise ValueError('Ambient audio must have a nonempty channel axis')
    if target_channels == 1:
        if audio_data.shape[1] == 1:
            return audio_data
        return np.mean(audio_data, axis=1, keepdims=True).astype(np.float32)
    if audio_data.shape[1] == target_channels:
        return audio_data
    if audio_data.shape[1] == 1:
        return np.repeat(audio_data, target_channels, axis=1).astype(np.float32)
    if audio_data.shape[1] > target_channels:
        return audio_data[:, :target_channels].astype(np.float32)
    output = np.zeros((audio_data.shape[0], target_channels), dtype=np.float32)
    output[:, :audio_data.shape[1]] = audio_data
    return output


def _coerce_array_for_mixer(int_data: np.ndarray, target_channels: int) -> np.ndarray:
    """Enforce the final PCM boundary, without a second channel remapping.

    Mono keeps one sample per frame, not a flattened multichannel timeline.
    Mismatched columns fail explicitly; strided arrays become C-contiguous.
    """
    if target_channels < 1:
        raise ValueError('Ambient target channel count must be positive')
    normalized = np.asarray(int_data, dtype=np.int16)
    if normalized.ndim == 1 and target_channels == 1:
        return np.ascontiguousarray(normalized)
    if normalized.ndim != 2 or normalized.shape[1] != target_channels:
        raise ValueError('Ambient PCM shape does not match mixer channels')
    if target_channels == 1:
        normalized = normalized[:, 0]
    return np.ascontiguousarray(normalized)


class _MixerFormatChanged(RuntimeError):
    """A Sound cannot be returned under a format other than its preparation cut."""


def _check_mixer_format(current: Tuple[int, int, int] | None,
                        expected: Tuple[int, int, int]) -> None:
    if current != expected:
        raise _MixerFormatChanged(
            f'Ambient mixer format changed during preparation: {expected!r} -> {current!r}'
        )


def _prepare_signed16_sound(self, source_path: Path,
                            mixer_info: Tuple[int, int, int]) -> 'pygame.mixer.Sound':
    """Prepare signed-16 PCM or execute the existing explicit file-load recovery.

    Shape/dtype/format diagnostics cover boundary failures without sample contents.
    Nonfinite/empty samples are rejected before PCM conversion. Observable mixer
    changes bypass recovery; process-control/allocation failures are never caught.
    """
    ambient_module = _ambient_module()
    pygame_api = ambient_module.pygame
    mixer_rate, _mixer_size, mixer_channels = mixer_info
    mixer_ready_array = None
    try:
        audio_data, sample_rate = self._load_raw_audio(source_path)
        processed = self._resample_linear(audio_data, int(sample_rate), mixer_rate)
        processed = self._match_mixer_channels(processed, mixer_channels)
        if processed.shape[0] == 0 or not np.isfinite(processed).all():
            raise ValueError('Ambient audio must contain finite, nonempty samples')
        int_data = np.asarray(np.clip(processed, -1.0, 1.0) * 32767.0, dtype=np.int16)
        mixer_ready_array = _coerce_array_for_mixer(int_data, mixer_channels)
        _check_mixer_format(pygame_api.mixer.get_init(), mixer_info)
        self._logger.debug('Ambient PCM prepared: mixer=%r shape=%r dtype=%s',
                           mixer_info, mixer_ready_array.shape, mixer_ready_array.dtype)
        return pygame_api.sndarray.make_sound(mixer_ready_array)
    except _MixerFormatChanged:
        raise
    except (ValueError, TypeError) + ambient_module.FILE_EXCEPTIONS + ambient_module.PYGAME_EXCEPTIONS as exc:
        _check_mixer_format(pygame_api.mixer.get_init(), mixer_info)
        self._logger.debug(
            'Ambient processing fallback to pygame.Sound for %s: %s; mixer=%r shape=%r dtype=%s',
            source_path, exc, mixer_info,
            None if mixer_ready_array is None else mixer_ready_array.shape,
            None if mixer_ready_array is None else mixer_ready_array.dtype,
            exc_info=True,
        )
        try:
            return pygame_api.mixer.Sound(str(source_path))
        except ambient_module.FILE_EXCEPTIONS + ambient_module.PYGAME_EXCEPTIONS:
            self._logger.debug('Ambient direct pygame.Sound fallback failed for %s',
                               source_path, exc_info=True)
            raise


def _prepare_sound(self, source_path: Path) -> 'pygame.mixer.Sound':
    """Bind cached sounds to the actual rate/channel/sample format, not requests.

    Non-signed16 formats use pygame's file decoder explicitly instead of incorrectly
    scaled int16 arrays. Changes visible before/after construction are rejected.
    Same-format mixer restart/ABA and mutation after return need lifecycle ownership;
    these readbacks do not claim atomic protection against arbitrary reinitialization.
    """
    pygame_api = _ambient_module().pygame
    if not self._ensure_mixer_ready():
        raise RuntimeError('Ambient mixer not available')
    mixer_info = pygame_api.mixer.get_init()
    if not mixer_info:
        raise RuntimeError('Ambient mixer not initialized')
    mixer_rate, mixer_size, mixer_channels = mixer_info
    if mixer_rate <= 0 or mixer_channels <= 0:
        raise RuntimeError('Ambient mixer reports an invalid rate or channel count')
    cache_key = (str(source_path.resolve()).lower(), mixer_rate, mixer_channels, mixer_size)
    if cache_key in self._prepared_cache:
        sound = self._prepared_cache[cache_key]
    elif mixer_size == -16:
        sound = _prepare_signed16_sound(self, source_path, mixer_info)
    else:
        self._logger.debug('Ambient direct file loading for mixer format=%r', mixer_info)
        _check_mixer_format(pygame_api.mixer.get_init(), mixer_info)
        sound = pygame_api.mixer.Sound(str(source_path))
    _check_mixer_format(pygame_api.mixer.get_init(), mixer_info)
    self._prepared_cache[cache_key] = sound
    return sound
