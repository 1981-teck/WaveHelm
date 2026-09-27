from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.model.setting_manager import SettingsManager
from src.utils.exceptions import SettingsError
import src.model.setting_manager as setting_manager_module


RUNTIME_ROOT = Path(__file__).resolve().parent / '_settings_runtime'


def _make_manager(monkeypatch, subdir: str) -> SettingsManager:
    runtime_dir = RUNTIME_ROOT / subdir
    shutil.rmtree(runtime_dir, ignore_errors=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(setting_manager_module, 'get_user_data_dir', lambda: runtime_dir)
    return SettingsManager()


def test_load_settings_creates_defaults_when_missing(monkeypatch):
    manager = _make_manager(monkeypatch, 'missing_defaults')

    assert manager.SETTINGS_FILE.exists()
    assert manager.get_setting('volume') == 70
    assert Path(manager.get_setting('cache_dir')).exists()


def test_load_settings_merges_existing_values(monkeypatch):
    runtime_dir = RUNTIME_ROOT / 'merge_existing'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    settings_file = runtime_dir / 'settings.json'
    settings_file.write_text(json.dumps({'language': 'it', 'volume': 55}), encoding='utf-8')
    monkeypatch.setattr(setting_manager_module, 'get_user_data_dir', lambda: runtime_dir)

    manager = SettingsManager()

    assert manager.get_setting('language') == 'it'
    assert manager.get_setting('volume') == 55
    assert manager.get_setting('theme') == 'System'


def test_load_settings_falls_back_on_invalid_json(monkeypatch):
    runtime_dir = RUNTIME_ROOT / 'invalid_json'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / 'settings.json').write_text('{invalid', encoding='utf-8')
    monkeypatch.setattr(setting_manager_module, 'get_user_data_dir', lambda: runtime_dir)

    manager = SettingsManager()

    assert manager.get_setting('language') == 'en'
    assert manager.get_setting('volume') == 70


def test_set_setting_normalizes_ranges_but_rejects_wrong_numeric_types(monkeypatch):
    manager = _make_manager(monkeypatch, 'normalize_ranges')

    manager.set_setting('volume', 500)
    manager.set_setting('video_target_fps', -10)
    manager.set_setting('video_frame_queue_size', 500)
    original_file = manager.SETTINGS_FILE.read_bytes()

    with pytest.raises(SettingsError, match="must be a JSON integer"):
        manager.set_setting('video_decode_threads', 'bad')

    assert manager.get_setting('volume') == 100
    assert manager.get_setting('video_target_fps') == 1
    assert manager.get_setting('video_frame_queue_size') == 100
    assert manager.get_setting('video_decode_threads') == 0
    assert manager.SETTINGS_FILE.read_bytes() == original_file


def test_get_all_settings_returns_copy_and_reset_restores_defaults(monkeypatch):
    manager = _make_manager(monkeypatch, 'copy_reset')
    manager.set_setting('language', 'fr')

    snapshot = manager.get_all_settings()
    snapshot['language'] = 'es'
    assert manager.get_setting('language') == 'fr'

    manager.reset_to_defaults()
    assert manager.get_setting('language') == 'en'


def test_ambient_muted_helpers(monkeypatch):
    manager = _make_manager(monkeypatch, 'ambient_helpers')
    assert manager.is_ambient_muted() is False

    manager.set_ambient_muted(True)
    assert manager.is_ambient_muted() is True


def test_custom_settings_keys_persist_across_manager_reloads(monkeypatch):
    runtime_dir = RUNTIME_ROOT / 'custom_keys_persist'
    shutil.rmtree(runtime_dir, ignore_errors=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(setting_manager_module, 'get_user_data_dir', lambda: runtime_dir)

    manager = SettingsManager()
    manager.set_setting('ui_library_column_widths', [111, 222, 333])
    manager.set_setting('ui_playlist_track_column_widths', [444, 555])

    reloaded = SettingsManager()

    assert reloaded.get_setting('ui_library_column_widths') == [111, 222, 333]
    assert reloaded.get_setting('ui_playlist_track_column_widths') == [444, 555]


def test_load_settings_removes_unknown_keys_without_discarding_known_values(monkeypatch):
    runtime_dir = RUNTIME_ROOT / 'sanitize_unknown_keys'
    shutil.rmtree(runtime_dir, ignore_errors=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    settings_file = runtime_dir / 'settings.json'
    settings_file.write_text(
        json.dumps({'language': 'it', 'volume': 55, 'unknown_future_key': 'active'}),
        encoding='utf-8',
    )
    monkeypatch.setattr(setting_manager_module, 'get_user_data_dir', lambda: runtime_dir)

    manager = SettingsManager()
    persisted = json.loads(settings_file.read_text(encoding='utf-8'))

    assert manager.get_setting('language') == 'it'
    assert manager.get_setting('volume') == 55
    assert manager.get_setting('unknown_future_key') is None
    assert 'unknown_future_key' not in persisted


def test_invalid_boolean_snapshot_fails_closed_to_canonical_defaults(monkeypatch):
    runtime_dir = RUNTIME_ROOT / 'invalid_boolean_snapshot'
    shutil.rmtree(runtime_dir, ignore_errors=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    settings_file = runtime_dir / 'settings.json'
    settings_file.write_text(
        json.dumps({'language': 'it', 'video_hw_accel_enabled': 'false'}),
        encoding='utf-8',
    )
    monkeypatch.setattr(setting_manager_module, 'get_user_data_dir', lambda: runtime_dir)

    manager = SettingsManager()
    persisted = json.loads(settings_file.read_text(encoding='utf-8'))

    assert manager.get_setting('language') == 'en'
    assert manager.get_setting('video_hw_accel_enabled') is True
    assert persisted['video_hw_accel_enabled'] is True


def test_unknown_direct_setting_is_rejected_without_state_or_disk_change(monkeypatch):
    manager = _make_manager(monkeypatch, 'reject_unknown_direct')
    original_snapshot = manager.get_all_settings()
    original_file = manager.SETTINGS_FILE.read_bytes()

    with pytest.raises(SettingsError, match='Unsupported setting'):
        manager.set_setting('unknown_runtime_key', {'enabled': True})

    assert manager.get_all_settings() == original_snapshot
    assert manager.SETTINGS_FILE.read_bytes() == original_file


def test_get_setting_returns_isolated_nested_values(monkeypatch):
    manager = _make_manager(monkeypatch, 'isolated_get_setting')
    manager.set_setting('ambient_presets', {'rain': [0.2]})

    returned = manager.get_setting('ambient_presets')
    assert isinstance(returned, dict)
    rain = returned['rain']
    assert isinstance(rain, list)
    rain[0] = 0.9

    assert manager.get_setting('ambient_presets') == {'rain': [0.2]}

def test_import_with_mixed_valid_and_invalid_types_is_atomic(monkeypatch):
    manager = _make_manager(monkeypatch, 'typed_import_atomicity')
    original_snapshot = manager.get_all_settings()
    original_file = manager.SETTINGS_FILE.read_bytes()

    with pytest.raises(SettingsError, match="must be a JSON boolean"):
        manager.apply_imported_settings(
            {'volume': 42, 'video_hw_accel_enabled': 'false'}
        )

    assert manager.get_all_settings() == original_snapshot
    assert manager.SETTINGS_FILE.read_bytes() == original_file


def test_load_canonicalization_failure_does_not_retry_with_destructive_defaults(
    monkeypatch,
):
    runtime_dir = RUNTIME_ROOT / 'canonicalization_write_failure'
    shutil.rmtree(runtime_dir, ignore_errors=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    settings_file = runtime_dir / 'settings.json'
    settings_file.write_text(json.dumps({'language': 'it'}), encoding='utf-8')
    original_file = settings_file.read_bytes()
    monkeypatch.setattr(setting_manager_module, 'get_user_data_dir', lambda: runtime_dir)
    replace_calls = 0

    def fail_once(_source: object, _destination: object) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 1:
            raise PermissionError('injected canonicalization failure')
        raise AssertionError('default recovery must not retry after commit failure')

    monkeypatch.setattr(setting_manager_module, 'durable_replace', fail_once)

    manager = SettingsManager()

    assert replace_calls == 1
    assert manager.get_setting('language') == 'it'
    assert settings_file.read_bytes() == original_file
