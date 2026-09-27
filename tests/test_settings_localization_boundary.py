from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from src.audio.audio_events import AudioEventType
from src.controller.settings_controller import SettingsController
from src.model.localization_manager import LocalizationManager
from src.model.setting_manager import SettingsImportResult, SettingsManager
from src.utils.exceptions import SettingsError
import src.model.setting_manager as setting_manager_module


class _EventBus:
    def __init__(self) -> None:
        self.events: list[tuple[AudioEventType, dict[str, object]]] = []
        self.subscribers: dict[AudioEventType, list[Callable[[dict[str, object]], None]]] = {}

    def subscribe(
        self,
        event_type: AudioEventType,
        callback: Callable[[dict[str, object]], None],
    ) -> None:
        self.subscribers.setdefault(event_type, []).append(callback)

    def unsubscribe(
        self,
        event_type: AudioEventType,
        callback: Callable[[dict[str, object]], None],
    ) -> None:
        callbacks = self.subscribers.get(event_type, [])
        if callback in callbacks:
            callbacks.remove(callback)

    def publish(self, event_type: AudioEventType, payload: dict[str, object]) -> None:
        self.events.append((event_type, dict(payload)))


class _ThemeManager:
    def __init__(self) -> None:
        self.applied: list[tuple[str, str]] = []

    def set_theme(self, theme: str, primary_color: str) -> None:
        self.applied.append((theme, primary_color))


class _AudioEngine:
    def __init__(self) -> None:
        self.volume = 0.0

    def set_volume(self, volume: float) -> None:
        self.volume = volume


def _write_locale(path: Path, language_name: str, token: str) -> None:
    path.write_text(
        json.dumps({'_language_name': language_name, 'token': token}),
        encoding='utf-8',
    )


def _build_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[SettingsController, SettingsManager, LocalizationManager, _EventBus]:
    app_data = tmp_path / 'app_data'
    shutil.rmtree(app_data, ignore_errors=True)
    app_data.mkdir(parents=True)
    locales = tmp_path / 'locales'
    locales.mkdir(parents=True, exist_ok=True)
    _write_locale(locales / 'en.json', 'English', 'SAFE')
    _write_locale(locales / 'fr.json', 'Français', 'BONJOUR')
    monkeypatch.setattr(setting_manager_module, 'get_user_data_dir', lambda: app_data)

    localization = LocalizationManager(locales_dir=locales)
    settings = SettingsManager(localization_manager=localization)
    events = _EventBus()
    controller = SettingsController(
        settings,
        localization,
        _ThemeManager(),
        events,
        _AudioEngine(),
    )
    events.events.clear()
    return controller, settings, localization, events


def _event_types(events: _EventBus) -> list[AudioEventType]:
    return [event_type for event_type, _payload in events.events]


def test_import_rejects_unavailable_language_before_state_or_disk_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller, settings, localization, events = _build_runtime(monkeypatch, tmp_path)
    import_path = tmp_path / 'unsupported.json'
    import_path.write_text(json.dumps({'language': 'de', 'volume': 42}), encoding='utf-8')
    before_snapshot = settings.get_all_settings()
    before_file = settings.SETTINGS_FILE.read_bytes()

    controller.import_settings(str(import_path))

    assert settings.get_all_settings() == before_snapshot
    assert settings.SETTINGS_FILE.read_bytes() == before_file
    assert localization.get_current_language() == 'en'
    assert AudioEventType.ERROR in _event_types(events)
    assert AudioEventType.SETTINGS_BATCH_UPDATED not in _event_types(events)


@pytest.mark.parametrize(
    'language',
    ('../secret', r'..\secret', '/absolute/secret', r'C:\secret', '%2e%2e%2fsecret'),
)
def test_import_rejects_path_like_language_without_reading_outside_locale_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    language: str,
) -> None:
    controller, settings, localization, events = _build_runtime(monkeypatch, tmp_path)
    (tmp_path / 'secret.json').write_text(
        json.dumps({'_language_name': 'Secret', 'token': 'OUTSIDE'}),
        encoding='utf-8',
    )
    import_path = tmp_path / 'path_like.json'
    import_path.write_text(json.dumps({'language': language}), encoding='utf-8')
    before_file = settings.SETTINGS_FILE.read_bytes()

    controller.import_settings(str(import_path))

    assert settings.get_setting('language') == 'en'
    assert settings.SETTINGS_FILE.read_bytes() == before_file
    assert localization.get_current_language() == 'en'
    assert localization.get_text('token') == 'SAFE'
    assert AudioEventType.SETTINGS_BATCH_UPDATED not in _event_types(events)


def test_valid_import_canonicalizes_and_activates_language_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller, settings, localization, events = _build_runtime(monkeypatch, tmp_path)
    import_path = tmp_path / 'valid.json'
    import_path.write_text(
        json.dumps({'language': 'FR', 'volume': 42}),
        encoding='utf-8',
    )

    controller.import_settings(str(import_path))

    persisted = json.loads(settings.SETTINGS_FILE.read_text(encoding='utf-8'))
    assert settings.get_setting('language') == 'fr'
    assert persisted['language'] == 'fr'
    assert localization.get_current_language() == 'fr'
    assert localization.get_text('token') == 'BONJOUR'
    assert AudioEventType.SETTINGS_BATCH_UPDATED in _event_types(events)


def test_direct_language_update_is_atomic_when_locale_becomes_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _controller, settings, localization, _events = _build_runtime(monkeypatch, tmp_path)
    before_snapshot = settings.get_all_settings()
    before_file = settings.SETTINGS_FILE.read_bytes()
    (localization.get_locales_dir() / 'fr.json').write_text('[]', encoding='utf-8')

    with pytest.raises(SettingsError, match='available valid locale'):
        settings.set_setting('language', 'fr')

    assert settings.get_all_settings() == before_snapshot
    assert settings.SETTINGS_FILE.read_bytes() == before_file
    assert localization.get_current_language() == 'en'


def test_persisted_unavailable_language_recovers_to_valid_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app_data = tmp_path / 'app_data'
    app_data.mkdir()
    (app_data / 'settings.json').write_text(
        json.dumps({'language': 'de', 'volume': 42}),
        encoding='utf-8',
    )
    locales = tmp_path / 'locales'
    locales.mkdir()
    _write_locale(locales / 'en.json', 'English', 'SAFE')
    monkeypatch.setattr(setting_manager_module, 'get_user_data_dir', lambda: app_data)

    settings = SettingsManager(
        localization_manager=LocalizationManager(locales_dir=locales)
    )
    persisted = json.loads(settings.SETTINGS_FILE.read_text(encoding='utf-8'))

    assert settings.get_setting('language') == 'en'
    assert settings.get_setting('volume') == 42
    assert persisted['language'] == 'en'
    assert persisted['volume'] == 42



def test_language_runtime_activation_occurs_only_after_persistence_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller, settings, localization, events = _build_runtime(monkeypatch, tmp_path)
    before_file = settings.SETTINGS_FILE.read_bytes()

    def fail_replace(_source: object, _destination: object) -> None:
        raise PermissionError('injected persistence failure')

    monkeypatch.setattr(setting_manager_module, 'durable_replace', fail_replace)
    controller.set_setting('language', 'fr')

    assert settings.get_setting('language') == 'en'
    assert settings.SETTINGS_FILE.read_bytes() == before_file
    assert localization.get_current_language() == 'en'
    assert AudioEventType.ERROR in _event_types(events)
    assert AudioEventType.SETTINGS_UPDATED not in _event_types(events)


def test_prepared_import_snapshot_survives_post_commit_locale_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller, settings, localization, events = _build_runtime(monkeypatch, tmp_path)
    import_path = tmp_path / 'prepared.json'
    import_path.write_text(json.dumps({'language': 'fr'}), encoding='utf-8')
    original_apply = settings.apply_imported_settings

    def commit_then_replace_locale(document: object) -> SettingsImportResult:
        result = original_apply(document)
        (localization.get_locales_dir() / 'fr.json').write_text('[]', encoding='utf-8')
        return result

    settings.apply_imported_settings = commit_then_replace_locale
    controller.import_settings(str(import_path))

    assert settings.get_setting('language') == 'fr'
    assert localization.get_current_language() == 'fr'
    assert localization.get_text('token') == 'BONJOUR'
    assert AudioEventType.SETTINGS_BATCH_UPDATED in _event_types(events)


def test_language_updates_return_canonical_code_and_unrelated_writes_remain_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _controller, settings, localization, _events = _build_runtime(monkeypatch, tmp_path)

    persisted_language = settings.set_setting('language', 'FR')
    (localization.get_locales_dir() / 'fr.json').write_text('[]', encoding='utf-8')
    persisted_volume = settings.set_setting('volume', 44)

    assert persisted_language == 'fr'
    assert persisted_volume == 44
    assert settings.get_setting('language') == 'fr'
    assert settings.get_setting('volume') == 44

def test_controller_rejects_mismatched_production_localization_managers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    locales_a = tmp_path / 'locales-a'
    locales_b = tmp_path / 'locales-b'
    locales_a.mkdir()
    locales_b.mkdir()
    _write_locale(locales_a / 'en.json', 'English', 'A')
    _write_locale(locales_b / 'en.json', 'English', 'B')
    monkeypatch.setattr(setting_manager_module, 'get_user_data_dir', lambda: str(tmp_path / 'data'))
    settings_localization = LocalizationManager(locales_dir=locales_a)
    settings = SettingsManager(localization_manager=settings_localization)

    with pytest.raises(ValueError, match='must be identical'):
        SettingsController(
            settings_manager=settings,
            localization_manager=LocalizationManager(locales_dir=locales_b),
            theme_manager=_ThemeManager(),
            event_bus=_EventBus(),
            audio_engine=_AudioEngine(),
        )



def test_falsey_localization_manager_is_not_silently_replaced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FalseyLocalizationManager(LocalizationManager):
        def __bool__(self) -> bool:
            return False

    locales = tmp_path / 'locales-falsey'
    locales.mkdir()
    _write_locale(locales / 'en.json', 'English', 'SAFE')
    monkeypatch.setattr(setting_manager_module, 'get_user_data_dir', lambda: tmp_path / 'data')
    localization = FalseyLocalizationManager(locales_dir=locales)

    settings = SettingsManager(localization_manager=localization)

    assert settings.localization_manager is localization
