from __future__ import annotations

import json
import shutil
from pathlib import Path

from src.model.setting_manager import SettingsManager
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


def test_set_setting_normalizes_numeric_ranges(monkeypatch):
    manager = _make_manager(monkeypatch, 'normalize_ranges')

    manager.set_setting('volume', 500)
    manager.set_setting('video_target_fps', -10)
    manager.set_setting('video_frame_queue_size', 500)
    manager.set_setting('video_decode_threads', 'bad')

    assert manager.get_setting('volume') == 100
    assert manager.get_setting('video_target_fps') == 1
    assert manager.get_setting('video_frame_queue_size') == 100
    assert manager.get_setting('video_decode_threads') == 0


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
