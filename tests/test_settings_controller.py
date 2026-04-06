from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path

from src.audio.audio_events import AudioEventType
import src.controller.settings_controller as settings_controller_mod
import src.controller.settings_controller_cleanup as settings_cleanup_mod
from src.controller.settings_controller import SettingsController


class DummySettingsManager:
    def __init__(self):
        self.data = {
            'volume': 65,
            'language': 'it',
            'theme': 'Dark',
            'primary_color': 'green',
            'video_hw_accel_enabled': False,
            'video_drop_late_frames': True,
            'video_frame_queue_size': 7,
            'video_decode_threads': 2,
            'video_fast_seek': False,
            'video_resize_quality_high': True,
        }
        self.set_calls = []
        self.reset_calls = 0

    def get_all_settings(self):
        return dict(self.data)

    def set_setting(self, key, value):
        self.set_calls.append((key, value))
        self.data[key] = value

    def get_setting(self, key, default=None):
        return self.data.get(key, default)

    def reset_to_defaults(self):
        self.reset_calls += 1
        self.data = {
            'volume': 70,
            'language': 'en',
            'theme': 'System',
            'primary_color': 'blue',
        }


class DummyLocalizationManager:
    def __init__(self):
        self.current_language = 'en'
        self.set_calls = []

    def get_text(self, key, default=None, **kwargs):
        mapping = {
            'settings_controller_initialized': 'initialized',
            'setting_updated': 'updated {name}',
            'settings_exported': 'exported {path}',
            'settings_imported': 'imported {path}',
            'settings_reset_to_defaults': 'reset done',
            'error_setting_value': 'setting error {key}',
            'error_importing_settings': 'import error',
            'error_exporting_settings': 'export error',
            'error_resetting_settings': 'reset error',
            'settings_processed_audio_busy': 'Stop the current audio playback before clearing processed audio cache.',
        }
        text = mapping.get(key, default or key)
        return text.format(**kwargs) if kwargs else text

    def set_language(self, language):
        self.set_calls.append(language)
        self.current_language = language

    def get_current_language(self):
        return self.current_language


class DummyThemeManager:
    def __init__(self):
        self.calls = []

    def set_theme(self, theme, primary):
        self.calls.append((theme, primary))


class DummyEventBus:
    def __init__(self):
        self.published = []
        self.subscribed = []
        self.unsubscribed = []

    def publish(self, event_type, payload):
        self.published.append((event_type, payload))

    def subscribe(self, event_type, callback):
        self.subscribed.append((event_type, callback))

    def unsubscribe(self, event_type, callback):
        self.unsubscribed.append((event_type, callback))


class DummyAudioEngine:
    def __init__(self):
        self.volume_calls = []
        self.volume = None
        self._current_file = None
        self._playback_source_file = None

    def set_volume(self, value):
        self.volume_calls.append(value)


class DummyVideoPlayer:
    def __init__(self):
        self.volume_calls = []
        self.hw_calls = []
        self.runtime_calls = []
        self.volume = None

    def set_volume(self, value):
        self.volume_calls.append(value)

    def set_hw_accel(self, enabled):
        self.hw_calls.append(enabled)

    def configure_video_runtime(self, **kwargs):
        self.runtime_calls.append(kwargs)


class AttrOnlyTarget:
    def __init__(self):
        self.volume = None


RUNTIME_DIR = Path(__file__).resolve().parent / '_settings_runtime'
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _make_controller(video_player=None):
    settings = DummySettingsManager()
    localization = DummyLocalizationManager()
    theme = DummyThemeManager()
    bus = DummyEventBus()
    audio = DummyAudioEngine()
    video = video_player if video_player is not None else DummyVideoPlayer()
    controller = SettingsController(
        settings_manager=settings,
        localization_manager=localization,
        theme_manager=theme,
        event_bus=bus,
        audio_engine=audio,
        video_player=video,
    )
    return controller, settings, localization, theme, bus, audio, video


def test_init_applies_settings_and_subscribes_runtime_targets():
    controller, settings, localization, theme, bus, audio, video = _make_controller()

    assert controller is not None
    assert bus.subscribed[0][0] == AudioEventType.SETTINGS_UPDATED
    assert audio.volume_calls == [0.65]
    assert video.volume_calls == [0.65]
    assert localization.set_calls == ['it']
    assert theme.calls == [('Dark', 'green')]
    assert video.hw_calls == [False]
    assert video.runtime_calls == [
        {
            'drop_late_frames': True,
            'frame_queue_size': 7,
            'decode_threads': 2,
            'fast_seek': False,
            'resize_quality_high': True,
        }
    ]
    assert bus.published[0] == (
        AudioEventType.LANGUAGE_CHANGED,
        {'language': 'it'},
    )


def test_set_setting_persists_and_publishes_feedback():
    controller, settings, localization, theme, bus, audio, video = _make_controller()
    bus.published.clear()

    controller.set_setting('volume', 55)

    assert settings.set_calls[-1] == ('volume', 55)
    assert bus.published[0] == (
        AudioEventType.SETTINGS_UPDATED,
        {'key': 'volume', 'value': 55},
    )
    assert bus.published[1] == (
        AudioEventType.FEEDBACK_MESSAGE,
        {'message': 'updated volume', 'color': 'green'},
    )


def test_export_and_import_settings_publish_batch_events():
    controller, settings, localization, theme, bus, audio, video = _make_controller()
    export_path = RUNTIME_DIR / 'settings_export.json'
    import_path = RUNTIME_DIR / 'settings_import.json'
    if export_path.exists():
        export_path.unlink()
    import_path.write_text(
        json.dumps({'language': 'fr', 'volume': 42, 'theme': 'Light', 'primary_color': 'red'}),
        encoding='utf-8',
    )

    controller.export_settings(str(export_path))
    exported = json.loads(export_path.read_text(encoding='utf-8'))
    assert exported['volume'] == 65
    assert bus.published[-1] == (
        AudioEventType.FEEDBACK_MESSAGE,
        {'message': f'exported {export_path}', 'color': 'green'},
    )

    bus.published.clear()
    controller.import_settings(str(import_path))

    assert settings.data['language'] == 'fr'
    assert settings.data['volume'] == 42
    assert bus.unsubscribed[0][0] == AudioEventType.SETTINGS_UPDATED
    assert bus.subscribed[-1][0] == AudioEventType.SETTINGS_UPDATED
    assert (
        AudioEventType.SETTINGS_BATCH_UPDATED,
        {'updated_keys': ['language', 'volume', 'theme', 'primary_color'], 'source': 'import'},
    ) in bus.published
    assert bus.published[-1] == (
        AudioEventType.FEEDBACK_MESSAGE,
        {'message': f'imported {import_path}', 'color': 'green'},
    )


def test_reset_to_defaults_publishes_batch_update():
    controller, settings, localization, theme, bus, audio, video = _make_controller()
    bus.published.clear()

    controller.reset_to_defaults()

    assert settings.reset_calls == 1
    assert (
        AudioEventType.SETTINGS_BATCH_UPDATED,
        {
            'updated_keys': ['volume', 'language', 'theme', 'primary_color'],
            'source': 'reset',
        },
    ) in bus.published
    assert bus.published[-1] == (
        AudioEventType.FEEDBACK_MESSAGE,
        {'message': 'reset done', 'color': 'green'},
    )


def test_on_settings_updated_volume_falls_back_to_plain_attributes():
    settings = DummySettingsManager()
    localization = DummyLocalizationManager()
    theme = DummyThemeManager()
    bus = DummyEventBus()
    audio = AttrOnlyTarget()
    video = AttrOnlyTarget()
    controller = SettingsController(
        settings_manager=settings,
        localization_manager=localization,
        theme_manager=theme,
        event_bus=bus,
        audio_engine=audio,
        video_player=video,
    )

    controller._on_settings_updated({'key': 'volume', 'value': 35})

    assert audio.volume == 0.35
    assert video.volume == 0.35


def test_on_settings_updated_language_and_theme_refresh_runtime():
    controller, settings, localization, theme, bus, audio, video = _make_controller()
    bus.published.clear()
    theme.calls.clear()
    settings.data['theme'] = 'Light'
    settings.data['primary_color'] = 'yellow'

    controller._on_settings_updated({'key': 'language', 'value': 'es'})
    controller._on_settings_updated({'key': 'theme', 'value': 'Light'})

    assert localization.current_language == 'es'
    assert bus.published[0] == (
        AudioEventType.LANGUAGE_CHANGED,
        {'language': 'es'},
    )
    assert theme.calls == [('Light', 'yellow')]


def test_on_settings_updated_reports_runtime_language_failures():
    controller, settings, localization, theme, bus, audio, video = _make_controller()
    bus.published.clear()

    def broken_set_language(language):
        raise RuntimeError('boom')

    localization.set_language = broken_set_language

    controller._on_settings_updated({'key': 'language', 'value': 'es'})

    assert bus.published == [
        (
            AudioEventType.ERROR,
            {'message': 'setting error language: boom'},
        )
    ]


def test_clear_processed_audio_cache_and_runtime_artifacts_keep_user_files():
    controller, settings, localization, theme, bus, audio, video = _make_controller()
    runtime_dir = RUNTIME_DIR / f'cleanup_{uuid.uuid4().hex}'
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir, ignore_errors=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    settings.SETTINGS_FILE = runtime_dir / 'settings.json'
    settings.SETTINGS_FILE.write_text('{}', encoding='utf-8')
    app_data = settings.SETTINGS_FILE.parent

    processed_dir = app_data / 'processed_audio'
    cache_dir = app_data / 'cache'
    pycache_dir = app_data / 'pycache'
    logs_dir = app_data / 'logs'
    for directory in (processed_dir, cache_dir, pycache_dir, logs_dir):
        directory.mkdir(parents=True, exist_ok=True)

    (processed_dir / 'a.wav').write_text('x', encoding='utf-8')
    (cache_dir / 'cache.bin').write_text('x', encoding='utf-8')
    (pycache_dir / 'module.pyc').write_text('x', encoding='utf-8')
    (logs_dir / 'wavehelm.log').write_text('log', encoding='utf-8')
    (app_data / 'library.json').write_text('[]', encoding='utf-8')
    (app_data / 'wavehelm.db').write_text('db', encoding='utf-8')

    settings.data['cache_dir'] = str(cache_dir)
    audio._processed_audio_dir = processed_dir

    assert controller.get_app_data_dir() == app_data.resolve()
    assert controller.get_processed_audio_dir() == processed_dir.resolve()
    assert controller.get_logs_dir() == logs_dir.resolve()
    assert controller.clear_processed_audio_cache() == 1

    (processed_dir / 'b.wav').write_text('x', encoding='utf-8')
    result = controller.clear_runtime_artifacts()

    assert result == {
        'processed_audio_removed': 0,
        'cache_removed': 0,
        'logs_removed': 0,
        'total_removed': 0,
    }
    assert [p.name for p in processed_dir.iterdir()] == ['b.wav']
    assert [p.name for p in cache_dir.iterdir()] == ['cache.bin']
    assert [p.name for p in pycache_dir.iterdir()] == ['module.pyc']
    assert [p.name for p in logs_dir.iterdir()] == ['wavehelm.log']

    settings_cleanup_mod._run_pending_runtime_cleanup()

    assert [p.name for p in processed_dir.iterdir()] == ['b.wav']
    assert list(cache_dir.iterdir()) == []
    assert list(pycache_dir.iterdir()) == []
    assert list(logs_dir.iterdir()) == []
    assert (app_data / 'library.json').exists()
    assert (app_data / 'wavehelm.db').exists()

    shutil.rmtree(runtime_dir, ignore_errors=True)


def test_clear_runtime_artifacts_truncates_active_log_handler_on_shutdown_cleanup():
    controller, settings, localization, theme, bus, audio, video = _make_controller()
    runtime_dir = RUNTIME_DIR / f'cleanup_{uuid.uuid4().hex}'
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir, ignore_errors=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    settings.SETTINGS_FILE = runtime_dir / 'settings.json'
    settings.SETTINGS_FILE.write_text('{}', encoding='utf-8')
    app_data = settings.SETTINGS_FILE.parent
    logs_dir = app_data / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / 'wavehelm.log'
    log_path.write_text('before', encoding='utf-8')

    audio._processed_audio_dir = app_data / 'processed_audio'
    settings.data['cache_dir'] = str(app_data / 'cache')

    handler = logging.FileHandler(log_path, encoding='utf-8')
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        result = controller.clear_runtime_artifacts()
        assert result['logs_removed'] == 0
        assert log_path.exists()
        assert log_path.read_text(encoding='utf-8') == 'before'

        settings_cleanup_mod._run_pending_runtime_cleanup()

        assert log_path.exists()
        assert log_path.read_text(encoding='utf-8') == ''

        record = logging.LogRecord('test', logging.INFO, __file__, 0, 'after', (), None)
        handler.emit(record)
        handler.flush()
        assert 'after' in log_path.read_text(encoding='utf-8')
    finally:
        root.removeHandler(handler)
        handler.close()
        shutil.rmtree(runtime_dir, ignore_errors=True)


def test_processed_audio_cache_cleanup_is_blocked_while_processed_file_is_active():
    controller, settings, localization, theme, bus, audio, video = _make_controller()
    runtime_dir = RUNTIME_DIR / f'busy_{uuid.uuid4().hex}'
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir, ignore_errors=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    try:
        settings.SETTINGS_FILE = runtime_dir / 'settings.json'
        settings.SETTINGS_FILE.write_text('{}', encoding='utf-8')
        processed_dir = runtime_dir / 'processed_audio'
        cache_dir = runtime_dir / 'cache'
        logs_dir = runtime_dir / 'logs'
        processed_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
        processed_file = processed_dir / 'track.wav'
        processed_file.write_text('x', encoding='utf-8')
        (cache_dir / 'temp.bin').write_text('x', encoding='utf-8')
        (logs_dir / 'wavehelm.log').write_text('x', encoding='utf-8')

        audio._current_file = 'C:/music/source.wav'
        audio._playback_source_file = str(processed_file)
        audio._processed_audio_dir = processed_dir
        settings.data['cache_dir'] = str(cache_dir)

        assert controller.is_processed_audio_cache_in_use() is True
        assert controller.get_processed_audio_cleanup_block_message() == 'Stop the current audio playback before clearing processed audio cache.'

        try:
            controller.clear_processed_audio_cache()
            assert False, 'expected RuntimeError'
        except RuntimeError as error:
            assert str(error) == 'Stop the current audio playback before clearing processed audio cache.'

        result = controller.clear_runtime_artifacts()
        assert result['total_removed'] == 0
        settings_cleanup_mod._run_pending_runtime_cleanup()
        assert processed_file.exists()
        assert list(cache_dir.iterdir()) == []
        assert list(logs_dir.iterdir()) == []
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)


def test_pending_runtime_cleanup_is_consumed_after_one_shutdown_pass():
    controller, settings, localization, theme, bus, audio, video = _make_controller()
    runtime_dir = RUNTIME_DIR / f'oneshot_{uuid.uuid4().hex}'
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir, ignore_errors=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    try:
        settings.SETTINGS_FILE = runtime_dir / 'settings.json'
        settings.SETTINGS_FILE.write_text('{}', encoding='utf-8')
        cache_dir = runtime_dir / 'cache'
        logs_dir = runtime_dir / 'logs'
        pycache_dir = runtime_dir / 'pycache'
        for directory in (cache_dir, logs_dir, pycache_dir):
            directory.mkdir(parents=True, exist_ok=True)
        (cache_dir / 'first.bin').write_text('x', encoding='utf-8')
        (logs_dir / 'wavehelm.log').write_text('x', encoding='utf-8')
        (pycache_dir / 'module.pyc').write_text('x', encoding='utf-8')

        settings.data['cache_dir'] = str(cache_dir)
        controller.clear_runtime_artifacts()
        settings_cleanup_mod._run_pending_runtime_cleanup()

        (cache_dir / 'second.bin').write_text('x', encoding='utf-8')
        (logs_dir / 'second.log').write_text('x', encoding='utf-8')
        (pycache_dir / 'second.pyc').write_text('x', encoding='utf-8')
        settings_cleanup_mod._run_pending_runtime_cleanup()

        assert [p.name for p in cache_dir.iterdir()] == ['second.bin']
        assert [p.name for p in logs_dir.iterdir()] == ['second.log']
        assert [p.name for p in pycache_dir.iterdir()] == ['second.pyc']
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)


def test_path_getters_log_and_fallback_when_resolution_fails(caplog):
    controller, settings, localization, theme, bus, audio, video = _make_controller()
    caplog.set_level(logging.DEBUG, logger=settings_controller_mod.logger.name)

    class BrokenPathValue:
        def __str__(self):
            raise TypeError('broken path value')

    settings.SETTINGS_FILE = object()
    settings.data['cache_dir'] = BrokenPathValue()
    audio._processed_audio_dir = object()

    assert controller.get_app_data_dir() == Path.cwd()
    assert controller.get_processed_audio_dir() == (Path.cwd() / 'processed_audio').resolve()

    assert 'Failed to resolve app data dir from SETTINGS_FILE' in caplog.text
    assert 'Failed to resolve app data dir from cache_dir' in caplog.text
    assert 'Failed to resolve processed audio dir from audio engine' in caplog.text
