from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pygame

from src.audio.audio_events import AudioEventType
from src.audio import audio_engine_playback as playback
from src.audio import audio_engine_progress as progress


class DummyEventBus:
    def __init__(self):
        self.published = []
        self.unsubscribed = []

    def publish(self, event_type, payload):
        self.published.append((event_type, payload))

    def unsubscribe(self, event_type, callback=None, subscription=None, subscription_id=None):
        self.unsubscribed.append((event_type, callback, subscription, subscription_id))
        return False


class DummyLocalization:
    def get_text(self, key, default=None, **kwargs):
        text = default or key
        return text.format(**kwargs) if kwargs else text


class DummySettings:
    def __init__(self, value=70):
        self.value = value
        self.saved = []

    def get_setting(self, key, default=None):
        return self.value if key == 'volume' else default

    def set_setting(self, key, value):
        self.saved.append((key, value))
        if key == 'volume':
            self.value = value


class DummyEngine:
    def __init__(self):
        self.event_bus = DummyEventBus()
        self.localization_manager = DummyLocalization()
        self.settings_manager = DummySettings()
        self._get_localized_text = lambda key, default=None, **kwargs: (default or key).format(**kwargs) if kwargs else (default or key)
        self._cancel_dsp_refresh_timer = lambda: None
        self._ensure_audio_mixer = lambda: True
        self._load_audio_source = lambda path, playback_path=None: True
        self._start_progress_loop_called = False
        self._start_progress_loop = lambda: setattr(self, '_start_progress_loop_called', True)
        self._stop_progress_loop = lambda: setattr(self, '_stop_progress_loop_called', True)
        self._close_soundfile = lambda: setattr(self, '_close_soundfile_called', True)
        self.stop = lambda: setattr(self, '_stop_called', True)
        self._cleanup_processed_audio_dir = lambda: setattr(self, '_cleanup_called', True) or 1
        self._on_video_duration_update = lambda data=None: None
        self._on_eq_changed = lambda data=None: None
        self._on_effects_changed = lambda data=None: None
        self._on_volume_changed_event = lambda data=None: None
        self._on_settings_batch_updated = lambda data=None: None
        self._dsp_render_lock = SimpleNamespace(__enter__=lambda s: None, __exit__=lambda s, exc_type, exc, tb: False)
        self._current_file = None
        self._playback_source_file = None
        self._total_length = 0.0
        self._current_position = 0.0
        self._seek_base = 0.0
        self._is_muted = False
        self._volume = 0.5
        self._loop_enabled = False
        self._current_play_uses_native_loop = False
        self._is_paused = False
        self._is_music_loaded = False
        self._video_spectrum_feedback_sent = False
        self._no_file_spectrum_feedback_sent = False
        self._sf_file = None
        self.audio_analyzer = SimpleNamespace(fft_size=8, analyze=lambda chunk: chunk)
        self._progress_thread = None
        self._progress_stop_event = SimpleNamespace(set=lambda: None)
        self._was_playing = False
        self._progress_publish_interval = 0.1
        self._spectrum_publish_interval = 0.04
        self._poll_sleep_interval = 0.02
        self.equalizer = None
        self.effects_engine = None


class FakeMusic:
    def __init__(self):
        self.play_calls = []
        self.stop_calls = 0
        self.pause_calls = 0
        self.unpause_calls = 0
        self.set_volume_calls = []
        self.set_pos_calls = []
        self.busy = False
        self.pos_ms = -1

    def play(self, *args, **kwargs):
        self.play_calls.append((args, kwargs))

    def stop(self):
        self.stop_calls += 1

    def pause(self):
        self.pause_calls += 1

    def unpause(self):
        self.unpause_calls += 1

    def set_volume(self, value):
        self.set_volume_calls.append(value)

    def set_pos(self, value):
        self.set_pos_calls.append(value)

    def get_busy(self):
        return self.busy

    def get_pos(self):
        return self.pos_ms


def _install_fake_music(monkeypatch, module, music=None, mixer_init=True):
    fake_music = music or FakeMusic()
    monkeypatch.setattr(module.pygame.mixer, 'music', fake_music, raising=False)
    monkeypatch.setattr(module.pygame.mixer, 'get_init', lambda: mixer_init, raising=False)
    return fake_music


def test_estimate_duration_uses_soundfile_info(monkeypatch):
    monkeypatch.setattr(playback.sf, 'info', lambda path: SimpleNamespace(frames=88200, samplerate=44100))
    monkeypatch.setattr(
        playback,
        'read_audio_basic_metadata',
        lambda path: (_ for _ in ()).throw(AssertionError('metadata fallback should not be called')),
    )
    duration = playback._estimate_duration_seconds('track.wav', Path('track.wav'))
    assert duration == 2.0


def test_estimate_duration_falls_back_to_audio_metadata_reader(monkeypatch):
    monkeypatch.setattr(playback.sf, 'info', lambda path: (_ for _ in ()).throw(RuntimeError('boom')))
    monkeypatch.setattr(
        playback,
        'read_audio_basic_metadata',
        lambda path: SimpleNamespace(duration=3.5),
    )
    duration = playback._estimate_duration_seconds('track.mp3', Path('track.mp3'))
    assert duration == 3.5


def test_set_file_video_publishes_feedback_and_quits_mixer(monkeypatch):
    engine = DummyEngine()
    quit_calls = []
    monkeypatch.setattr(playback.pygame.mixer, 'get_init', lambda: True, raising=False)
    monkeypatch.setattr(playback.pygame.mixer, 'quit', lambda: quit_calls.append(True), raising=False)

    result = playback.set_file(engine, 'demo.mp4')

    assert result is False
    assert quit_calls == [True]
    published_types = [event_type for event_type, _ in engine.event_bus.published]
    assert published_types == [
        AudioEventType.MEDIA_LOADED,
        AudioEventType.PLAYER_STATE_CHANGED,
        AudioEventType.FEEDBACK_MESSAGE,
    ]


def test_play_ignores_video_files_without_starting_mixer(monkeypatch):
    engine = DummyEngine()
    engine._current_file = 'movie.mkv'
    fake_music = _install_fake_music(monkeypatch, playback)

    playback.play(engine)

    assert fake_music.play_calls == []
    assert engine.event_bus.published[-1][0] == AudioEventType.FEEDBACK_MESSAGE


def test_seek_falls_back_when_legacy_seek_path_fails(monkeypatch):
    engine = DummyEngine()
    engine._current_file = 'song.wav'

    class LegacySeekMusic(FakeMusic):
        def play(self, *args, **kwargs):
            self.play_calls.append((args, kwargs))
            if 'start' in kwargs:
                raise TypeError('legacy pygame')

        def set_pos(self, value):
            self.set_pos_calls.append(value)
            raise pygame.error('set_pos failed')

    fake_music = _install_fake_music(monkeypatch, playback, LegacySeekMusic())
    playback.seek(engine, 42.0)

    assert fake_music.stop_calls == 1
    assert engine._current_position == 0.0
    assert engine._seek_base == 0.0
    assert engine._start_progress_loop_called is True
    assert engine.event_bus.published[-1] == (AudioEventType.SEEK, {'position': 42.0})


def test_set_volume_updates_state_and_publishes_without_mixer(monkeypatch):
    engine = DummyEngine()
    _install_fake_music(monkeypatch, progress, mixer_init=False)

    progress.set_volume(engine, 0.7)

    assert engine._volume == 0.7
    assert engine.event_bus.published[-1] == (AudioEventType.VOLUME_CHANGED, {'volume': 0.7})


def test_set_mute_publishes_even_when_mixer_raises(monkeypatch):
    engine = DummyEngine()

    class BrokenMusic(FakeMusic):
        def set_volume(self, value):
            raise pygame.error('volume failed')

    _install_fake_music(monkeypatch, progress, BrokenMusic())
    progress.set_mute(engine, True)

    assert engine._is_muted is True
    assert engine.event_bus.published[-1] == (AudioEventType.MUTE_CHANGED, {'muted': True})


def test_play_and_seek_honor_loop_setting(monkeypatch):
    engine = DummyEngine()
    engine._current_file = 'song.wav'
    engine._loop_enabled = True
    fake_music = _install_fake_music(monkeypatch, playback)

    playback.play(engine)
    playback.seek(engine, 12.0)

    assert fake_music.play_calls[0][1]['loops'] == -1
    assert fake_music.play_calls[-1][1]['loops'] == -1


def test_set_loop_updates_mode_without_restarting_current_song(monkeypatch):
    engine = DummyEngine()
    engine._current_file = 'song.wav'
    engine._current_position = 7.5
    fake_music = _install_fake_music(monkeypatch, progress)
    monkeypatch.setattr(engine, 'is_playing', lambda: True, raising=False)
    monkeypatch.setattr(engine, 'get_position', lambda: 7.5, raising=False)

    progress.set_loop(engine, True)

    assert engine._loop_enabled is True
    assert engine._current_play_uses_native_loop is False
    assert engine._seek_base == 0.0
    assert fake_music.play_calls == []


def test_get_position_wraps_after_audio_loop(monkeypatch):
    engine = DummyEngine()
    engine._current_file = 'song.wav'
    engine._loop_enabled = True
    engine._current_play_uses_native_loop = True
    engine._total_length = 10.0
    fake_music = _install_fake_music(monkeypatch, playback)
    fake_music.pos_ms = 12500

    position = playback.get_position(engine)

    assert round(position, 3) == 2.5


def test_progress_loop_position_wraps_when_loop_enabled(monkeypatch):
    engine = DummyEngine()
    engine._current_file = 'song.wav'
    engine._loop_enabled = True
    engine._current_play_uses_native_loop = True
    engine._total_length = 10.0
    engine._progress_publish_interval = 0.0
    engine._spectrum_publish_interval = 999.0
    engine._poll_sleep_interval = 0.001
    engine._sf_file = None

    fake_music = _install_fake_music(monkeypatch, progress)
    fake_music.busy = True
    fake_music.pos_ms = 12500

    calls = {'n': 0}
    def fake_wait(interval):
        calls['n'] += 1
        if calls['n'] == 1:
            return False
        engine._progress_stop_event.set()
        return True

    engine._progress_stop_event = SimpleNamespace(set=lambda: setattr(engine, '_stop_set', True), clear=lambda: None, is_set=lambda: getattr(engine, '_stop_set', False), wait=fake_wait)

    progress._start_progress_loop(engine)
    engine._progress_thread.join(timeout=1.0)

    assert round(engine._current_position, 3) == 2.5
    assert any(evt == AudioEventType.PLAYBACK_PROGRESS and round(payload['current_time'], 3) == 2.5 for evt, payload in engine.event_bus.published)


def test_close_cleans_processed_audio_dir(monkeypatch):
    engine = DummyEngine()
    _install_fake_music(monkeypatch, playback)

    playback.close(engine)

    assert engine._stop_called is True
    assert engine._close_soundfile_called is True
    assert engine._cleanup_called is True


def test_set_volume_persists_setting_value(monkeypatch):
    engine = DummyEngine()
    _install_fake_music(monkeypatch, progress, mixer_init=False)

    progress.set_volume(engine, 0.42)

    assert engine.settings_manager.saved == [('volume', 42)]
    assert engine.settings_manager.get_setting('volume') == 42
