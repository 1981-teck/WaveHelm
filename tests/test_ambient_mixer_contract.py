"""Real ambient helpers/manager; recording mixer boundary is NOT native pygame.

Cover missing channels without inventing surround content, mono frame preservation,
actual-vs-requested mixer format, cache separation, and changes during preparation.
Real SoundFile decoding/export uses tiny synthetic files; no user media is accessed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from src.model import ambient_manager as ambient
from src.model import ambient_manager_audio as audio


class MixerFailure(Exception):
    pass


class MixerBoundary:
    """Records API calls and enforces documented rank/depth; no audio playback."""

    def __init__(self, info=(44100, -16, 2)):
        self.info = info
        self.arrays = []
        self.direct = []
        self.array_error = None
        self.direct_error = None
        self.after_array = None
        self.after_direct = None

    def get_init(self):
        return self.info

    def make_sound(self, array):
        self.arrays.append(array.copy())
        if self.array_error is not None:
            raise self.array_error
        channels = self.info[2]
        expected = (array.shape[0],) if channels == 1 else (array.shape[0], channels)
        if array.shape != expected:
            raise ValueError('Array depth must match number of mixer channels')
        if array.dtype != np.int16 or not array.flags.c_contiguous:
            raise ValueError('Expected contiguous signed-16 PCM')
        if self.after_array is not None:
            self.info = self.after_array
        return SimpleNamespace(origin='array', samples=array.copy())

    def Sound(self, path):
        self.direct.append(path)
        if self.direct_error is not None:
            raise self.direct_error
        if self.after_direct is not None:
            self.info = self.after_direct
        return SimpleNamespace(origin='file')


def make_manager(monkeypatch, samples, info=(44100, -16, 2), rate=44100):
    mixer = MixerBoundary(info)
    monkeypatch.setattr(ambient, 'pygame', SimpleNamespace(mixer=mixer, sndarray=mixer))
    monkeypatch.setattr(ambient, 'PYGAME_EXCEPTIONS', (AttributeError, MixerFailure))
    # Skip library/UI initialization, but use the real bound manager methods.
    manager = ambient.AmbientManager.__new__(ambient.AmbientManager)
    manager._logger = logging.getLogger('ambient.contract')
    manager._prepared_cache = {}
    manager._raw_cache = {}
    manager._ensure_mixer_ready = lambda: True
    manager._load_raw_audio = lambda path: (samples, rate)
    manager._volume = 0.4
    manager._muted = False
    return manager, mixer


def expected_channels(samples, target):
    if target == 1:
        return samples.mean(axis=1, keepdims=True).astype(np.float32)
    if samples.shape[1] == 1:
        return np.repeat(samples, target, axis=1)
    expected = np.zeros((len(samples), target), dtype=np.float32)
    count = min(samples.shape[1], target)
    expected[:, :count] = samples[:, :count]
    return expected


@pytest.mark.parametrize('source_channels', [1, 2, 4, 6, 8])
@pytest.mark.parametrize('target_channels', [1, 2, 4, 6, 8])
def test_channel_mapping_preserves_frames_values_and_input(source_channels, target_channels):
    samples = np.linspace(-0.75, 0.75, 8 * source_channels, dtype=np.float32).reshape(8, -1)
    before = samples.copy()
    result = audio._match_mixer_channels(None, samples, target_channels)
    np.testing.assert_array_equal(result, expected_channels(samples, target_channels))
    np.testing.assert_array_equal(samples, before)
    assert result.shape == (8, target_channels)
    ready = audio._coerce_array_for_mixer((result * 32767).astype(np.int16), target_channels)
    assert ready.shape == ((8,) if target_channels == 1 else (8, target_channels))
    assert ready.dtype == np.int16 and ready.flags.c_contiguous


@pytest.mark.parametrize('target', [0, -1])
def test_nonpositive_channel_target_rejected(target):
    with pytest.raises(ValueError):
        audio._match_mixer_channels(None, np.ones((4, 2), dtype=np.float32), target)
    with pytest.raises(ValueError):
        audio._coerce_array_for_mixer(np.ones((4, 2), dtype=np.int16), target)


@pytest.mark.parametrize('shape', [(4,), (2, 3, 1), (4, 0)])
def test_invalid_source_channel_layout_rejected(shape):
    with pytest.raises(ValueError):
        audio._match_mixer_channels(None, np.zeros(shape, dtype=np.float32), 2)


@pytest.mark.parametrize('shape,target', [((4, 2), 1), ((4, 1), 2), ((4, 4), 2), ((4,), 2), ((2, 2, 2), 1)])
def test_final_pcm_guard_rejects_mismatch_instead_of_flattening_or_remixing(shape, target):
    with pytest.raises(ValueError):
        audio._coerce_array_for_mixer(np.zeros(shape, dtype=np.int16), target)


@pytest.mark.parametrize('target', [1, 2, 4, 6])
def test_noncontiguous_samples_are_copied_before_native_boundary(target):
    samples = np.arange(64, dtype=np.int16).reshape(8, 8)[::2, :target]
    before = samples.copy()
    ready = audio._coerce_array_for_mixer(samples, target)
    assert ready.flags.c_contiguous
    np.testing.assert_array_equal(ready.reshape(4, target), before)


@pytest.mark.parametrize('source_channels,target', [(2, 4), (2, 6), (4, 6), (1, 6), (6, 2), (2, 1)])
def test_prepare_sound_uses_negotiated_channels_without_fallback(monkeypatch, tmp_path, source_channels, target):
    samples = np.linspace(-0.5, 0.5, 8 * source_channels, dtype=np.float32).reshape(8, -1)
    manager, mixer = make_manager(monkeypatch, samples, (48000, -16, target), rate=24000)
    result = manager._prepare_sound(tmp_path / 'synthetic.wav')
    assert result.origin == 'array'
    assert mixer.direct == [] and len(mixer.arrays) == 1
    assert mixer.arrays[0].shape == ((16,) if target == 1 else (16, target))
    if 1 < source_channels < target:
        np.testing.assert_array_equal(mixer.arrays[0][:, source_channels:], 0)


@pytest.mark.parametrize('size', [-8, 8, 16, -32])
def test_non_signed16_formats_delegate_to_file_loader_explicitly(monkeypatch, tmp_path, caplog, size):
    manager, mixer = make_manager(monkeypatch, np.ones((8, 2), dtype=np.float32), (44100, size, 2))
    source = tmp_path / 'synthetic.wav'
    with caplog.at_level(logging.DEBUG):
        result = manager._prepare_sound(source)
    assert result.origin == 'file' and mixer.direct == [str(source)]
    assert mixer.arrays == []
    assert 'mixer format' in caplog.text


def test_cache_includes_actual_sample_format(monkeypatch, tmp_path):
    manager, mixer = make_manager(monkeypatch, np.ones((8, 2), dtype=np.float32))
    source = tmp_path / 'synthetic.wav'
    first = manager._prepare_sound(source)
    assert manager._prepare_sound(source) is first
    mixer.info = (44100, 8, 2)
    second = manager._prepare_sound(source)
    assert second is not first and second.origin == 'file'
    assert len(manager._prepared_cache) == 2
    assert {key[-1] for key in manager._prepared_cache} == {-16, 8}


@pytest.mark.parametrize('after', [(48000, -16, 2), (44100, -16, 6), (44100, 8, 2), None])
def test_change_during_decode_rejected_before_sound_creation(monkeypatch, tmp_path, after):
    samples = np.ones((8, 2), dtype=np.float32)
    manager, mixer = make_manager(monkeypatch, samples)
    def decode(path):
        mixer.info = after
        return samples, 44100
    manager._load_raw_audio = decode
    with pytest.raises(RuntimeError, match='mixer format changed'):
        manager._prepare_sound(tmp_path / 'synthetic.wav')
    assert mixer.arrays == [] and mixer.direct == [] and manager._prepared_cache == {}


@pytest.mark.parametrize('kind', ['array', 'file'])
def test_change_during_construction_not_cached_or_returned(monkeypatch, tmp_path, kind):
    size = -16 if kind == 'array' else 8
    manager, mixer = make_manager(monkeypatch, np.ones((8, 2), dtype=np.float32), (44100, size, 2))
    if kind == 'array':
        mixer.after_array = (48000, -16, 6)
    else:
        mixer.after_direct = (48000, -16, 6)
    with pytest.raises(RuntimeError, match='mixer format changed'):
        manager._prepare_sound(tmp_path / 'synthetic.wav')
    assert manager._prepared_cache == {}
    assert len(mixer.arrays) + len(mixer.direct) == 1


@pytest.mark.parametrize('error_type', [ValueError, TypeError, OSError, RuntimeError, MixerFailure])
def test_existing_decode_failure_fallback_preserved(monkeypatch, tmp_path, caplog, error_type):
    manager, mixer = make_manager(monkeypatch, np.ones((8, 2), dtype=np.float32))
    original = error_type('synthetic decode failure')
    def decode(path):
        raise original
    manager._load_raw_audio = decode
    with caplog.at_level(logging.DEBUG):
        result = manager._prepare_sound(tmp_path / 'synthetic.wav')
    assert result.origin == 'file' and len(mixer.direct) == 1 and mixer.arrays == []
    assert 'synthetic decode failure' in caplog.text and 'mixer=' in caplog.text


def test_boundary_failure_preserves_shape_dtype_and_actual_format_diagnostics(monkeypatch, tmp_path, caplog):
    manager, mixer = make_manager(monkeypatch, np.ones((8, 2), dtype=np.float32), (48000, -16, 6))
    mixer.array_error = ValueError('injected boundary error')
    with caplog.at_level(logging.DEBUG):
        result = manager._prepare_sound(tmp_path / 'synthetic.wav')
    assert result.origin == 'file' and len(mixer.direct) == 1
    for text in ['injected boundary error', 'mixer=(48000, -16, 6)', 'shape=', 'int16']:
        assert text in caplog.text


@pytest.mark.parametrize('error_type', [KeyboardInterrupt, SystemExit, MemoryError])
@pytest.mark.parametrize('phase', ['decode', 'array', 'file'])
def test_control_and_allocation_errors_propagate_unchanged(monkeypatch, tmp_path, error_type, phase):
    manager, mixer = make_manager(monkeypatch, np.ones((8, 2), dtype=np.float32))
    original = error_type('synthetic failure')
    def fail(path):
        raise original
    if phase == 'decode':
        manager._load_raw_audio = fail
    elif phase == 'array':
        mixer.array_error = original
    else:
        mixer.info = (44100, 8, 2)
        mixer.direct_error = original
    with pytest.raises(error_type) as caught:
        manager._prepare_sound(tmp_path / 'synthetic.wav')
    assert caught.value is original and manager._prepared_cache == {}
    assert len(mixer.direct) == (1 if phase == 'file' else 0)


@pytest.mark.parametrize('samples', [np.full((8, 2), np.nan), np.full((8, 2), np.inf)])
def test_nonfinite_decoded_audio_not_sent_to_sndarray(monkeypatch, tmp_path, samples):
    manager, mixer = make_manager(monkeypatch, samples)
    assert manager._prepare_sound(tmp_path / 'synthetic.wav').origin == 'file'
    assert mixer.arrays == []


def test_real_soundfile_decode_and_cache(monkeypatch, tmp_path):
    samples = np.linspace(-0.75, 0.75, 64, dtype=np.float32).reshape(32, 2)
    source = tmp_path / 'synthetic.wav'
    sf.write(source, samples, 24000, subtype='FLOAT')
    manager, mixer = make_manager(monkeypatch, samples, (48000, -16, 6))
    del manager._load_raw_audio
    result = manager._prepare_sound(source)
    assert result.origin == 'array' and result.samples.shape == (64, 6)
    assert mixer.direct == []
    assert manager._prepare_sound(source) is result and len(mixer.arrays) == 1
    np.testing.assert_array_equal(manager._raw_cache[str(source.resolve()).lower()][0], samples)


@pytest.mark.parametrize('target', [4, 6])
def test_real_multichannel_mix_export_preserves_unfilled_channels(monkeypatch, tmp_path, target):
    main = np.full((16, target), 0.25, dtype=np.float32)
    overlay = np.full((8, 2), 0.5, dtype=np.float32)
    main_file, overlay_file = tmp_path / 'main.wav', tmp_path / 'ambient.wav'
    sf.write(main_file, main, 44100, subtype='FLOAT')
    sf.write(overlay_file, overlay, 44100, subtype='FLOAT')
    manager, _mixer = make_manager(monkeypatch, overlay)
    manager._resolve_sound_path = lambda name: overlay_file
    destination = tmp_path / 'mixed.wav'
    assert manager.export_audio_mix(main_source_path=str(main_file), output_path=str(destination),
                                    ambient_sound_name_or_path='test') == destination
    result, rate = sf.read(destination, dtype='float32', always_2d=True)
    assert rate == 44100 and result.shape == main.shape
    np.testing.assert_allclose(result[:, :2], 0.45, atol=1/32768)
    np.testing.assert_allclose(result[:, 2:], 0.25, atol=1/32768)


@pytest.mark.parametrize('target', [1, 2, 4, 6])
def test_empty_helpers_keep_empty_time_axis(target):
    samples = np.empty((0, 2), dtype=np.float32)
    mapped = audio._match_mixer_channels(None, samples, target)
    assert mapped.shape == (0, target)
    ready = audio._coerce_array_for_mixer(mapped, target)
    assert ready.shape == ((0,) if target == 1 else (0, target))


def test_signed16_clipping_and_scale_are_byte_identical(monkeypatch, tmp_path):
    samples = np.array([[-2.0, 2.0], [-1.0, 1.0], [-0.5, 0.5], [0.0, 0.0]], dtype=np.float32)
    samples.setflags(write=False)
    manager, mixer = make_manager(monkeypatch, samples)
    sound = manager._prepare_sound(tmp_path / 'synthetic.wav')
    assert sound.samples.tobytes() == np.array([[-32767, 32767], [-32767, 32767],
                                               [-16383, 16383], [0, 0]], dtype=np.int16).tobytes()
    assert len(mixer.arrays) == 1 and mixer.direct == []


@pytest.mark.parametrize('info', [None, (0, -16, 2), (44100, -16, 0)])
def test_invalid_or_absent_mixer_rejected_without_loading(monkeypatch, tmp_path, info):
    manager, mixer = make_manager(monkeypatch, np.ones((8, 2), dtype=np.float32), info)
    with pytest.raises(RuntimeError):
        manager._prepare_sound(tmp_path / 'synthetic.wav')
    assert not mixer.arrays and not mixer.direct and not manager._prepared_cache


def test_unavailable_mixer_does_not_decode(monkeypatch, tmp_path):
    manager, mixer = make_manager(monkeypatch, np.ones((8, 2), dtype=np.float32))
    manager._ensure_mixer_ready = lambda: False
    with pytest.raises(RuntimeError, match='not available'):
        manager._prepare_sound(tmp_path / 'synthetic.wav')
    assert mixer.arrays == [] and mixer.direct == []


@pytest.mark.parametrize('error_type', [OSError, RuntimeError, MixerFailure])
def test_recovery_loader_error_identity_preserved(monkeypatch, tmp_path, error_type):
    manager, mixer = make_manager(monkeypatch, np.ones((8, 2), dtype=np.float32))
    mixer.array_error = ValueError('synthetic array error')
    original = error_type('synthetic recovery error')
    mixer.direct_error = original
    with pytest.raises(error_type) as caught:
        manager._prepare_sound(tmp_path / 'synthetic.wav')
    assert caught.value is original
    assert len(mixer.arrays) == len(mixer.direct) == 1
    assert manager._prepared_cache == {}


def test_recovery_not_attempted_after_concurrent_format_change(monkeypatch, tmp_path):
    manager, mixer = make_manager(monkeypatch, np.ones((8, 2), dtype=np.float32))
    def fail_and_change(array):
        mixer.info = (48000, -16, 6)
        raise ValueError('synthetic shape mismatch after format change')
    mixer.make_sound = fail_and_change
    with pytest.raises(RuntimeError, match='mixer format changed'):
        manager._prepare_sound(tmp_path / 'synthetic.wav')
    assert mixer.direct == [] and manager._prepared_cache == {}


def test_cached_result_is_rechecked_before_return(monkeypatch, tmp_path):
    manager, mixer = make_manager(monkeypatch, np.ones((8, 2), dtype=np.float32))
    source = tmp_path / 'synthetic.wav'
    manager._prepare_sound(source)
    calls = 0
    def get_init():
        nonlocal calls
        calls += 1
        return (44100, -16, 2) if calls == 1 else None
    mixer.get_init = get_init
    with pytest.raises(RuntimeError, match='mixer format changed'):
        manager._prepare_sound(source)
    assert len(mixer.arrays) == 1 and mixer.direct == []


@pytest.mark.parametrize('new_info', [(48000, -16, 2), (44100, -16, 6)])
def test_rate_and_channel_changes_get_separate_cached_sounds(monkeypatch, tmp_path, new_info):
    manager, mixer = make_manager(monkeypatch, np.ones((8, 2), dtype=np.float32))
    source = tmp_path / 'synthetic.wav'
    first = manager._prepare_sound(source)
    mixer.info = new_info
    second = manager._prepare_sound(source)
    assert first is not second
    assert len(mixer.arrays) == 2 and len(manager._prepared_cache) == 2


def test_empty_decode_uses_explicit_existing_recovery(monkeypatch, tmp_path):
    manager, mixer = make_manager(monkeypatch, np.empty((0, 2), dtype=np.float32))
    assert manager._prepare_sound(tmp_path / 'synthetic.wav').origin == 'file'
    assert mixer.arrays == [] and len(mixer.direct) == 1
