from __future__ import annotations

from pathlib import Path

TEST_DATA_ROOT = Path(__file__).resolve().parent / '_ambient_runtime'
TEST_DATA_ROOT.mkdir(parents=True, exist_ok=True)

from src.audio.audio_events import AudioEventType
from src.model import ambient_manager as ambient


class DummySettings:
    def __init__(self):
        self.values = {
            'ambient_volume': 0.4,
            'ambient_muted': False,
        }
        self.writes = []

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value
        self.writes.append((key, value))


class DummyEventBus:
    def __init__(self):
        self.published = []

    def publish(self, event_type, payload):
        self.published.append((event_type, payload))


class DummyLocalization:
    def get_text(self, key, default=None):
        return default or key


class FakeChannel:
    def __init__(self, *, fail_stop=False):
        self.fail_stop = fail_stop
        self.play_calls = []
        self.stop_calls = 0
        self.volume_calls = []
        self.busy = False

    def stop(self):
        self.stop_calls += 1
        if self.fail_stop:
            raise FakePygameError('stop failed')

    def play(self, sound, loops=0):
        self.play_calls.append((sound, loops))
        self.busy = True

    def set_volume(self, volume):
        self.volume_calls.append(volume)

    def get_busy(self):
        return self.busy


class FakeSound:
    def __init__(self, label):
        self.label = label


class FakeMixer:
    def __init__(self):
        self.initialized = (44100, -16, 2)
        self.num_channels = 2
        self.channel = FakeChannel()
        self.sound_calls = []

    def get_init(self):
        return self.initialized

    def init(self, frequency=44100, size=-16, channels=2):
        self.initialized = (frequency, size, channels)

    def get_num_channels(self):
        return self.num_channels

    def set_num_channels(self, value):
        self.num_channels = value

    def Channel(self, index):
        return self.channel

    def Sound(self, path):
        self.sound_calls.append(path)
        return FakeSound(path)


class FakeSndArray:
    def __init__(self):
        self.calls = []

    def make_sound(self, array):
        self.calls.append(array)
        return FakeSound('array-sound')


class FakePygameError(Exception):
    pass


class FakePygame:
    error = FakePygameError

    def __init__(self):
        self.mixer = FakeMixer()
        self.sndarray = FakeSndArray()


def _make_manager(monkeypatch):
    fake_pygame = FakePygame()
    monkeypatch.setattr(ambient, 'pygame', fake_pygame)
    monkeypatch.setattr(ambient, 'PYGAME_EXCEPTIONS', (AttributeError, FakePygameError))
    ambient_root = TEST_DATA_ROOT / 'ambient_sounds'
    ambient_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ambient, 'get_app_data_path', lambda *args, **kwargs: ambient_root)
    monkeypatch.setattr(ambient, 'is_audio_file', lambda path: path.endswith(('.wav', '.mp3', '.ogg')))
    manager = ambient.AmbientManager(
        settings_manager=DummySettings(),
        event_bus=DummyEventBus(),
        localization_manager=DummyLocalization(),
    )
    return manager, fake_pygame


def test_load_uses_sound_map_and_stores_current_name(monkeypatch):
    manager, _fake = _make_manager(monkeypatch)
    source = TEST_DATA_ROOT / 'rain.wav'
    manager._sound_map = {'Rain': source}
    manager._prepare_sound = lambda path: FakeSound('loaded')

    result = manager.load('Rain')

    assert result is True
    assert manager.get_current_sound_name() == 'Rain'
    assert manager.get_current_source() == str(source)
    assert isinstance(manager._sound, FakeSound)


def test_start_plays_in_loop_and_publishes_event(monkeypatch):
    manager, fake = _make_manager(monkeypatch)
    manager._sound = FakeSound('loop')
    manager._source_path = 'rain.wav'
    manager._current_name = 'Rain'

    result = manager.start()

    assert result is True
    assert fake.mixer.channel.stop_calls == 1
    assert fake.mixer.channel.play_calls == [(manager._sound, -1)]
    assert fake.mixer.channel.volume_calls[-1] == manager.get_ambient_volume()
    assert manager.event_bus.published[-1][0] == AudioEventType.AMBIENT_STARTED


def test_set_ambient_volume_updates_channel_and_persists(monkeypatch):
    manager, fake = _make_manager(monkeypatch)
    manager._channel = fake.mixer.channel

    manager.set_ambient_volume(0.75)

    assert manager.get_ambient_volume() == 0.75
    assert manager.settings_manager.writes[-1] == ('ambient_volume', 0.75)
    assert fake.mixer.channel.volume_calls[-1] == 0.75
    assert manager.event_bus.published[-1] == (AudioEventType.AMBIENT_VOLUME, {'volume': 0.75})


def test_ambient_speed_api_removed(monkeypatch):
    manager, _fake = _make_manager(monkeypatch)

    assert not hasattr(manager, 'set_ambient_speed')
    assert not hasattr(manager, 'get_ambient_speed')


def test_prepare_sound_falls_back_to_mixer_sound_when_speed_is_default(monkeypatch):
    manager, fake = _make_manager(monkeypatch)
    manager._speed = 1.0
    manager._load_raw_audio = lambda source_path: (_ for _ in ()).throw(RuntimeError('decode failed'))
    source = TEST_DATA_ROOT / 'rain.wav'

    sound = manager._prepare_sound(source)

    assert isinstance(sound, FakeSound)
    assert fake.mixer.sound_calls == [str(source)]


def test_stop_ambient_sound_publishes_even_when_channel_stop_fails(monkeypatch):
    manager, _fake = _make_manager(monkeypatch)
    manager._channel = FakeChannel(fail_stop=True)
    manager._source_path = 'rain.wav'
    manager._current_name = 'Rain'
    manager._enabled = True

    manager.stop_ambient_sound()

    assert manager._enabled is False
    assert manager.event_bus.published[-1] == (
        AudioEventType.AMBIENT_STOPPED,
        {'source': 'rain.wav', 'name': 'Rain'},
    )


def test_ambient_manager_logs_setting_read_failures(monkeypatch, caplog):
    manager, _fake = _make_manager(monkeypatch)
    caplog.set_level('DEBUG', logger=ambient.logger.name)

    class BrokenSettings:
        def get_setting(self, key, default=None):
            raise RuntimeError('settings fail')

    manager.settings_manager = BrokenSettings()

    assert manager._read_setting('ambient_volume', 0.4) == 0.4
    assert 'Failed to read ambient setting ambient_volume' in caplog.text


def test_set_ambient_muted_sets_effective_volume_and_event(monkeypatch):
    manager, fake = _make_manager(monkeypatch)
    manager._channel = fake.mixer.channel

    manager.set_ambient_muted(True)

    assert manager.is_ambient_muted() is True
    assert fake.mixer.channel.volume_calls[-1] == 0.0
    assert manager.event_bus.published[-1] == (
        AudioEventType.AMBIENT_MUTED_CHANGED,
        {'muted': True},
    )


def test_stop_ambient_sound_skips_channel_stop_when_mixer_is_not_initialized(monkeypatch):
    manager, fake = _make_manager(monkeypatch)
    fake.mixer.initialized = None
    manager._channel = fake.mixer.channel
    manager._source_path = 'rain.wav'
    manager._current_name = 'Rain'
    manager._enabled = True

    manager.stop_ambient_sound()

    assert fake.mixer.channel.stop_calls == 0
    assert manager._enabled is False
    assert manager.event_bus.published[-1] == (
        AudioEventType.AMBIENT_STOPPED,
        {'source': 'rain.wav', 'name': 'Rain'},
    )


def test_export_audio_mix_loops_overlay_and_respects_volumes(monkeypatch, tmp_path):
    manager, _fake = _make_manager(monkeypatch)
    main_path = tmp_path / 'main.wav'
    ambient_path = tmp_path / 'ambient.wav'
    output_path = tmp_path / 'ambient mix saved' / 'mix.wav'

    ambient.sf.write(str(main_path), [[0.5], [0.5], [0.5], [0.5]], 4)
    ambient.sf.write(str(ambient_path), [[0.25], [-0.25]], 4)

    manager._volume = 0.4

    exported = manager.export_audio_mix(
        main_source_path=str(main_path),
        output_path=str(output_path),
        ambient_sound_name_or_path=str(ambient_path),
        main_volume=0.5,
        main_muted=False,
    )

    mixed_audio, sample_rate = ambient.sf.read(str(exported), dtype='float32', always_2d=True)
    assert exported == output_path
    assert sample_rate == 4
    assert mixed_audio.shape == (4, 1)
    expected = [0.35, 0.15, 0.35, 0.15]
    for actual, target in zip(mixed_audio[:, 0].tolist(), expected):
        assert abs(actual - target) < 0.03
