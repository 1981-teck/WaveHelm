from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pygame

from src.audio import audio_engine_helpers as helpers
from src.audio.audio_events import AudioEventType


class DummySettings:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail

    def get_setting(self, key, default=None):
        if self.should_fail:
            raise ValueError('boom')
        return f'value:{key}'


class DummyLocalization:
    def __init__(self, should_fail=False, text='Hello {name}'):
        self.should_fail = should_fail
        self.text = text

    def get_text(self, key, default=None):
        if self.should_fail:
            raise KeyError(key)
        return self.text if default is None else self.text


class DummyEventBus:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.calls = []

    def subscribe(self, event_type, callback):
        if self.should_fail:
            raise RuntimeError('subscribe failed')
        self.calls.append((event_type, callback))


class DummyEqualizer:
    def __init__(self):
        self._sample_rate = None
        self._channels = None
        self.recalc_calls = 0

    def _recalculate_all_filters(self):
        self.recalc_calls += 1


class DummyEffects:
    def __init__(self):
        self.sample_rate = None
        self.channels = None


class DummySoundFile:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.closed = False

    def close(self):
        if self.should_fail:
            raise OSError('close failed')
        self.closed = True


class FakeMusic:
    def __init__(self):
        self.set_volume_calls = []

    def set_volume(self, value):
        self.set_volume_calls.append(value)


class DummyEngine:
    def __init__(self):
        self.settings_manager = DummySettings()
        self.localization_manager = DummyLocalization()
        self.event_bus = DummyEventBus()
        self.audio_config = SimpleNamespace(frequency=44100, size=-16, channels=2, buffer=512)
        self._is_muted = False
        self._volume = 0.5
        self._current_file = None
        self.equalizer = None
        self.effects_engine = None
        self._sf_file = None
        self.schedule_calls = 0
        self._dsp_active = False
        self._on_volume_changed_event = lambda payload=None: None
        self._on_settings_batch_updated = lambda payload=None: None
        self._on_video_duration_update = lambda payload=None: None
        self._on_eq_changed = lambda payload=None: None
        self._on_effects_changed = lambda payload=None: None

    def _is_dsp_processing_active(self):
        return self._dsp_active

    def _schedule_dsp_refresh(self):
        self.schedule_calls += 1


def test_get_setting_returns_value_and_falls_back_on_known_error():
    engine = DummyEngine()
    assert helpers._get_setting(engine, 'volume', 99) == 'value:volume'

    engine.settings_manager = DummySettings(should_fail=True)
    assert helpers._get_setting(engine, 'volume', 99) == 99


def test_get_localized_text_formats_and_falls_back_cleanly():
    engine = DummyEngine()
    assert helpers._get_localized_text(engine, 'greeting', name='Marco') == 'Hello Marco'

    engine.localization_manager = DummyLocalization(text='Hello {missing}')
    assert helpers._get_localized_text(engine, 'greeting', name='Marco') == 'Hello {missing}'

    engine.localization_manager = DummyLocalization(should_fail=True)
    assert helpers._get_localized_text(engine, 'greeting', default='Fallback {name}', name='Marco') == 'Fallback Marco'


def test_initialize_mixer_reinitializes_and_sets_volume(monkeypatch):
    engine = DummyEngine()
    fake_music = FakeMusic()
    init_calls = []
    quit_calls = []

    monkeypatch.setattr(helpers.pygame.mixer, 'music', fake_music, raising=False)
    monkeypatch.setattr(helpers.pygame.mixer, 'get_init', lambda: True, raising=False)
    monkeypatch.setattr(helpers.pygame.mixer, 'quit', lambda: quit_calls.append(True), raising=False)
    monkeypatch.setattr(helpers.pygame.mixer, 'init', lambda **kwargs: init_calls.append(kwargs), raising=False)

    helpers._initialize_mixer(engine)

    assert quit_calls == [True]
    assert init_calls == [{'frequency': 44100, 'size': -16, 'channels': 2, 'buffer': 512}]
    assert fake_music.set_volume_calls == [0.5]


def test_bind_dsp_processors_schedules_refresh_only_when_changed_and_active():
    engine = DummyEngine()
    engine._current_file = 'song.wav'
    engine._dsp_active = True
    eq = DummyEqualizer()
    fx = DummyEffects()

    helpers.bind_dsp_processors(engine, equalizer=eq, effects_engine=fx)
    helpers.bind_dsp_processors(engine, equalizer=eq, effects_engine=fx)

    assert engine.equalizer is eq
    assert engine.effects_engine is fx
    assert engine.schedule_calls == 1


def test_sync_dsp_context_updates_equalizer_and_effects():
    engine = DummyEngine()
    engine.equalizer = DummyEqualizer()
    engine.effects_engine = DummyEffects()

    helpers._sync_dsp_context(engine, 48000, 1)

    assert engine.equalizer._sample_rate == 48000
    assert engine.equalizer._channels == 1
    assert engine.equalizer.recalc_calls == 1
    assert engine.effects_engine.sample_rate == 48000
    assert engine.effects_engine.channels == 1


def test_subscribe_to_events_registers_expected_callbacks():
    engine = DummyEngine()

    helpers._subscribe_to_events(engine)

    event_types = [event_type for event_type, _ in engine.event_bus.calls]
    assert event_types == [
        AudioEventType.VOLUME_CHANGED,
        AudioEventType.SETTINGS_BATCH_UPDATED,
        AudioEventType.VIDEO_DURATION_UPDATE,
        AudioEventType.EQ_CHANGED,
        AudioEventType.EFFECTS_CHANGED,
    ]


def test_close_soundfile_clears_reference_even_on_error():
    engine = DummyEngine()
    sf_file = DummySoundFile(should_fail=True)
    engine._sf_file = sf_file

    helpers._close_soundfile(engine)

    assert engine._sf_file is None


def test_ensure_audio_mixer_initializes_and_reports_failure(monkeypatch):
    engine = DummyEngine()
    fake_music = FakeMusic()
    init_calls = []
    monkeypatch.setattr(helpers.pygame.mixer, 'music', fake_music, raising=False)
    monkeypatch.setattr(helpers.pygame.mixer, 'get_init', lambda: False, raising=False)
    monkeypatch.setattr(helpers.pygame.mixer, 'init', lambda **kwargs: init_calls.append(kwargs), raising=False)

    assert helpers._ensure_audio_mixer(engine) is True
    assert init_calls == [{'frequency': 44100, 'size': -16, 'channels': 2, 'buffer': 512}]
    assert fake_music.set_volume_calls == [0.5]

    monkeypatch.setattr(helpers.pygame.mixer, 'init', lambda **kwargs: (_ for _ in ()).throw(pygame.error('boom')), raising=False)
    monkeypatch.setattr(helpers.pygame.mixer, 'get_init', lambda: False, raising=False)
    assert helpers._ensure_audio_mixer(engine) is False


def test_resolve_processed_audio_dir_uses_first_writable_candidate(monkeypatch):
    runtime_root = Path(__file__).resolve().parent / '_audio_helpers_runtime'
    shutil.rmtree(runtime_root, ignore_errors=True)
    app_dir = runtime_root / 'appdata'
    temp_dir = runtime_root / 'tempdir'
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(helpers, 'get_app_data_path', lambda *args, **kwargs: app_dir)
    monkeypatch.setattr(helpers.tempfile, 'gettempdir', lambda: str(temp_dir))

    result = helpers._resolve_processed_audio_dir(DummyEngine())

    assert result == app_dir
    assert result.exists()
