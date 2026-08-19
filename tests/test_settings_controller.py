from __future__ import annotations

import json
from pathlib import Path

from src.audio.audio_events import AudioEventType
from src.controller.settings_controller import SettingsController
from src.model.setting_manager import SettingsImportResult
from src.utils.exceptions import SettingsError


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
        self.import_error = None

    def get_all_settings(self):
        return dict(self.data)

    def get_exportable_settings(self):
        return {key: value for key, value in self.data.items() if key != 'cache_dir'}

    def set_setting(self, key, value):
        self.set_calls.append((key, value))
        persisted_value = max(0, min(100, int(value))) if key == 'volume' else value
        self.data[key] = persisted_value
        return persisted_value

    def apply_imported_settings(self, values):
        if self.import_error is not None:
            raise self.import_error
        candidate = dict(self.data)
        updated_keys = []
        ignored_keys = []
        for key, value in values.items():
            if key == 'cache_dir':
                ignored_keys.append(key)
                continue
            candidate[key] = value
            updated_keys.append(key)
        self.data = candidate
        return SettingsImportResult(tuple(updated_keys), tuple(ignored_keys))

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


def test_set_setting_publishes_normalized_persisted_value():
    controller, settings, localization, theme, bus, audio, video = _make_controller()
    bus.published.clear()

    controller.set_setting('volume', 500)

    assert settings.data['volume'] == 100
    assert bus.published[0] == (
        AudioEventType.SETTINGS_UPDATED,
        {'key': 'volume', 'value': 100},
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
        {
            'updated_keys': ['language', 'volume', 'theme', 'primary_color'],
            'ignored_keys': [],
            'source': 'import',
        },
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


def test_set_setting_failure_publishes_only_error_feedback():
    controller, settings, localization, theme, bus, audio, video = _make_controller()
    bus.published.clear()

    def fail_set_setting(key, value):
        raise SettingsError('write failed')

    settings.set_setting = fail_set_setting
    controller.set_setting('volume', 55)

    assert bus.published == [
        (
            AudioEventType.ERROR,
            {'message': 'setting error volume: [SETTINGS_ERROR] write failed'},
        )
    ]


def test_import_failure_keeps_state_and_publishes_no_success(tmp_path):
    controller, settings, localization, theme, bus, audio, video = _make_controller()
    original = dict(settings.data)
    settings.import_error = SettingsError('import rejected')
    import_path = tmp_path / 'settings_import.json'
    import_path.write_text(json.dumps({'language': 'fr', 'volume': 42}), encoding='utf-8')
    bus.published.clear()

    controller.import_settings(str(import_path))

    assert settings.data == original
    assert bus.published == [
        (
            AudioEventType.ERROR,
            {'message': 'import error: [SETTINGS_ERROR] import rejected'},
        )
    ]
    assert bus.unsubscribed[-1][0] == AudioEventType.SETTINGS_UPDATED
    assert bus.subscribed[-1][0] == AudioEventType.SETTINGS_UPDATED
