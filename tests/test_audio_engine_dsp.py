from __future__ import annotations

import shutil
from pathlib import Path

import pygame

import src.audio.audio_engine_dsp as dsp


RUNTIME_ROOT = Path(__file__).resolve().parent / "_audio_engine_dsp_runtime"


def _runtime_dir(name: str) -> Path:
    target = RUNTIME_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


class NullLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DummyEqualizer:
    def __init__(self, state=None, fail=False):
        self.is_enabled = True
        self._state = state or {"band_gains": {"60": 0.0}}
        self.fail = fail

    def get_current_state(self):
        if self.fail:
            raise ValueError("bad eq")
        return self._state

    def apply_eq_to_chunk(self, data):
        return data


class DummyEffects:
    def __init__(self, settings=None, fail=False):
        self._settings = settings or {"echo": {"enabled": False}}
        self.fail = fail

    def get_current_settings(self):
        if self.fail:
            raise RuntimeError("bad fx")
        return self._settings

    def apply_effects(self, data):
        return data


class FakeMusic:
    def __init__(self, *, fail_load=False, fail_stop=False):
        self.fail_load = fail_load
        self.fail_stop = fail_stop
        self.load_calls = []
        self.volume_calls = []
        self.stop_calls = 0
        self.play_calls = []

    def load(self, path):
        self.load_calls.append(path)
        if self.fail_load:
            raise pygame.error("load failed")

    def set_volume(self, value):
        self.volume_calls.append(value)

    def stop(self):
        self.stop_calls += 1
        if self.fail_stop:
            raise pygame.error("stop failed")

    def play(self, *args, **kwargs):
        self.play_calls.append((args, kwargs))


class DummyTimer:
    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False
        self.fail_cancel = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True
        if self.fail_cancel:
            raise RuntimeError("cancel failed")


class DummyEngine:
    def __init__(self):
        self.equalizer = None
        self.effects_engine = None
        self._processed_audio_dir = Path('.')
        self._is_muted = False
        self._volume = 0.5
        self._is_music_loaded = False
        self._playback_source_file = None
        self._sf_file = None
        self._dsp_refresh_timer = None
        self._dsp_refresh_delay = 0.25
        self._refresh_dsp_playback_source = lambda: None
        self._current_file = None
        self._dsp_render_lock = NullLock()
        self._current_position = 0.0
        self._seek_base = 0.0
        self._total_length = 0.0
        self._is_paused = False
        self._loop_enabled = False
        self._current_play_uses_native_loop = False
        self._close_soundfile_calls = 0
        self._stop_progress_loop_called = False
        self._start_progress_loop_called = False
        self._is_playing_value = False
        self._is_paused_value = False
        self._position_value = 0.0
        self._has_active_equalizer = lambda: dsp._has_active_equalizer(self)
        self._has_active_effects = lambda: dsp._has_active_effects(self)
        self._is_dsp_processing_active = lambda: dsp._is_dsp_processing_active(self)
        self._cancel_dsp_refresh_timer = lambda: dsp._cancel_dsp_refresh_timer(self)

    def _close_soundfile(self):
        self._close_soundfile_calls += 1
        self._sf_file = None

    def _sync_dsp_context(self, sample_rate, channels):
        self._synced = (sample_rate, channels)

    def is_playing(self):
        return self._is_playing_value

    def is_paused(self):
        return self._is_paused_value

    def get_position(self):
        return self._position_value

    def _stop_progress_loop(self):
        self._stop_progress_loop_called = True

    def _start_progress_loop(self):
        self._start_progress_loop_called = True

    def _ensure_audio_mixer(self):
        return True


def test_dsp_state_detection_handles_eq_and_effect_failures():
    engine = DummyEngine()
    engine.equalizer = DummyEqualizer({"band_gains": {"60": 1.5}})
    engine.effects_engine = DummyEffects({"echo": {"enabled": True}})

    assert dsp._has_active_equalizer(engine) is True
    assert dsp._has_active_effects(engine) is True
    assert dsp._is_dsp_processing_active(engine) is True

    engine.equalizer = DummyEqualizer(fail=True)
    engine.effects_engine = DummyEffects(fail=True)

    assert dsp._has_active_equalizer(engine) is True
    assert dsp._has_active_effects(engine) is False


def test_resolve_playback_source_falls_back_when_render_fails():
    runtime_dir = _runtime_dir('resolve')
    engine = DummyEngine()
    source = runtime_dir / 'song.wav'
    engine._is_dsp_processing_active = lambda: False
    engine._render_processed_audio = lambda path: (_ for _ in ()).throw(AssertionError('should not render'))
    assert dsp._resolve_playback_source(engine, source) == source

    engine._is_dsp_processing_active = lambda: True
    engine._render_processed_audio = lambda path: (_ for _ in ()).throw(RuntimeError('boom'))
    assert dsp._resolve_playback_source(engine, source) == source


def test_load_audio_source_updates_state_and_resets_on_failure(monkeypatch):
    runtime_dir = _runtime_dir('load_audio')

    success_engine = DummyEngine()
    fake_music = FakeMusic()
    monkeypatch.setattr(dsp.pygame.mixer, 'music', fake_music, raising=False)
    monkeypatch.setattr(dsp.sf, 'SoundFile', lambda path, mode: f'sf:{Path(path).name}')

    assert dsp._load_audio_source(success_engine, runtime_dir / 'source.wav', playback_path=runtime_dir / 'processed.wav') is True
    assert success_engine._is_music_loaded is True
    assert success_engine._playback_source_file.endswith('processed.wav')
    assert success_engine._sf_file == 'sf:processed.wav'
    assert fake_music.volume_calls[-1] == 0.5

    failing_engine = DummyEngine()
    failing_music = FakeMusic(fail_load=True)
    monkeypatch.setattr(dsp.pygame.mixer, 'music', failing_music, raising=False)

    assert dsp._load_audio_source(failing_engine, runtime_dir / 'source.wav', playback_path=runtime_dir / 'broken.wav') is False
    assert failing_engine._is_music_loaded is False
    assert failing_engine._playback_source_file is None
    assert failing_engine._sf_file is None


def test_cancel_and_schedule_dsp_refresh(monkeypatch, caplog):
    engine = DummyEngine()
    timer = DummyTimer(0.25, lambda: None)
    timer.fail_cancel = True
    engine._dsp_refresh_timer = timer
    caplog.set_level('DEBUG', logger=dsp.logger.name)

    dsp._cancel_dsp_refresh_timer(engine)
    assert engine._dsp_refresh_timer is None
    assert timer.cancelled is True
    assert 'Failed to cancel DSP refresh timer cleanly' in caplog.text

    monkeypatch.setattr(dsp.threading, 'Timer', DummyTimer)
    engine._current_file = 'movie.mp4'
    dsp._schedule_dsp_refresh(engine)
    assert engine._dsp_refresh_timer is None

    engine._current_file = object()
    dsp._schedule_dsp_refresh(engine)
    assert engine._dsp_refresh_timer is None

    engine._current_file = 'song.wav'
    dsp._schedule_dsp_refresh(engine)
    assert isinstance(engine._dsp_refresh_timer, DummyTimer)
    assert engine._dsp_refresh_timer.started is True


def test_refresh_dsp_playback_source_pre_renders_before_stop_and_resumes(monkeypatch):
    runtime_dir = _runtime_dir('refresh')
    engine = DummyEngine()
    engine._current_file = str(runtime_dir / 'song.wav')
    engine._total_length = 30.0
    engine._position_value = 12.5
    engine._is_playing_value = True
    engine._is_paused_value = False

    order = []
    target_path = runtime_dir / 'processed.wav'

    def fake_resolve(path):
        order.append('resolve')
        return target_path

    def fake_load_audio(path, playback_path=None):
        order.append(('load', str(playback_path)))
        engine._playback_source_file = str(playback_path)
        return True

    class OrderedMusic(FakeMusic):
        def stop(self):
            assert order == ['resolve']
            super().stop()
            order.append('stop')

    engine._resolve_playback_source = fake_resolve
    engine._load_audio_source = fake_load_audio
    fake_music = OrderedMusic()
    monkeypatch.setattr(dsp.pygame.mixer, 'music', fake_music, raising=False)

    dsp._refresh_dsp_playback_source(engine)

    assert engine._stop_progress_loop_called is True
    assert fake_music.stop_calls == 1
    assert order[0] == 'resolve'
    assert order[1] == 'stop'
    assert order[2] == ('load', str(target_path))
    assert fake_music.play_calls[-1][1]['start'] == 12.5
    assert engine._current_position == 12.5
    assert engine._seek_base == 12.5
    assert engine._start_progress_loop_called is True
    assert engine._is_paused is False


def test_refresh_dsp_playback_source_skips_reload_when_target_source_is_unchanged(monkeypatch):
    runtime_dir = _runtime_dir('refresh_skip')
    engine = DummyEngine()
    engine._current_file = str(runtime_dir / 'song.wav')
    target_path = runtime_dir / 'processed.wav'
    engine._playback_source_file = str(target_path)

    load_calls = []
    engine._resolve_playback_source = lambda path: target_path
    engine._load_audio_source = lambda path, playback_path=None: load_calls.append((path, playback_path)) or True
    fake_music = FakeMusic()
    monkeypatch.setattr(dsp.pygame.mixer, 'music', fake_music, raising=False)

    dsp._refresh_dsp_playback_source(engine)

    assert fake_music.stop_calls == 0
    assert load_calls == []
    assert engine._stop_progress_loop_called is False
    assert engine._start_progress_loop_called is False
