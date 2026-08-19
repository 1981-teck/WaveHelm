from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import src.model.profile_manager as profile_module
from src.model.profile_manager import ProfileManager


RUNTIME_ROOT = Path(__file__).resolve().parent / "_profile_manager_runtime"


def _runtime_dir(name: str) -> Path:
    target = RUNTIME_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _make_manager(monkeypatch, name: str) -> tuple[ProfileManager, Path]:
    runtime_dir = _runtime_dir(name)
    monkeypatch.setattr(profile_module, 'PROFILE_PATH', runtime_dir / 'profile.json')
    return ProfileManager(), runtime_dir


def test_load_profile_reads_existing_json_and_handles_invalid_json(monkeypatch):
    runtime_dir = _runtime_dir('load_valid')
    profile_path = runtime_dir / 'profile.json'
    profile_path.write_text(json.dumps({
        'stats': {'plays': 2},
        'effects_settings': {'echo': {'enabled': True}},
        'eq_settings': {'band_gains': {'60': 1.0}},
        'custom_eq_presets': {'Rocky': {'60': 3}},
        'custom_effects_settings': {'Wide': {'reverb': True}},
        'home_stats_prefs': {'show_plays': True},
    }), encoding='utf-8')
    monkeypatch.setattr(profile_module, 'PROFILE_PATH', profile_path)

    manager = ProfileManager()
    assert manager.get_stat('plays') == 2
    assert manager.get_effects_settings() == {'echo': {'enabled': True}}
    assert manager.get_eq_settings() == {'band_gains': {'60': 1.0}}
    assert manager.get_custom_eq_presets() == {'Rocky': {'60': 3}}
    assert manager.get_custom_effects_settings() == {'Wide': {'reverb': True}}
    assert manager.get_home_stats_prefs() == {'show_plays': True}

    invalid_dir = _runtime_dir('load_invalid')
    invalid_path = invalid_dir / 'profile.json'
    invalid_path.write_text('{broken', encoding='utf-8')
    monkeypatch.setattr(profile_module, 'PROFILE_PATH', invalid_path)
    broken = ProfileManager()
    assert broken.get_stat('plays') == 0
    assert broken.get_home_stats_prefs() == {}


def test_save_persists_all_profile_sections(monkeypatch):
    manager, runtime_dir = _make_manager(monkeypatch, 'save_all')
    manager.increment_stat('plays', 3)
    manager.set_effects_settings({'echo': {'enabled': True}})
    manager.set_eq_settings({'band_gains': {'60': 2.5}})
    manager.save_custom_eq_preset('Rock', {'60': 3.0})
    manager.save_custom_effects_setting('Wide', {'reverb': True})
    manager.set_home_stats_prefs({'show_plays': True})

    data = json.loads((runtime_dir / 'profile.json').read_text(encoding='utf-8'))
    assert data['stats'] == {'plays': 3}
    assert data['effects_settings'] == {'echo': {'enabled': True}}
    assert data['eq_settings'] == {'band_gains': {'60': 2.5}}
    assert data['custom_eq_presets'] == {'Rock': {'60': 3.0}}
    assert data['custom_effects_settings'] == {'Wide': {'reverb': True}}
    assert data['home_stats_prefs'] == {'show_plays': True}


def test_getters_and_deletes_return_safe_values(monkeypatch):
    manager, _ = _make_manager(monkeypatch, 'getters')
    manager.save_custom_eq_preset('Rock', {'60': 3.0})
    manager.save_custom_effects_setting('Wide', {'reverb': True})

    assert manager.get_custom_eq_presets() == {'Rock': {'60': 3.0}}
    assert manager.get_custom_effects_settings() == {'Wide': {'reverb': True}}

    manager.delete_custom_eq_preset('Rock')
    manager.delete_custom_effects_setting('Wide')

    assert manager.get_custom_eq_presets() == {}
    assert manager.get_custom_effects_settings() == {}
    assert manager.get_effects_settings() == {}
    assert manager.get_eq_settings() == {}
    assert manager.get_home_stats_prefs() == {}


def test_stat_and_setting_helpers_are_resilient(monkeypatch):
    manager, _ = _make_manager(monkeypatch, 'resilient')
    manager.user_profile.stats['broken'] = 'x'
    manager.increment_stat('broken', 2)
    assert manager.get_stat('broken') == 0

    manager.profile = {'language': 'it'}
    manager.settings = {'theme': 'dark'}
    assert manager.get_profile_setting('language', 'en') == 'it'
    assert manager.get_profile_setting('theme', 'light') == 'dark'
    assert manager.get_profile_setting('missing', 'fallback') == 'fallback'


def test_get_profile_setting_logs_debug_on_broken_mapping(monkeypatch, caplog):
    manager, _ = _make_manager(monkeypatch, 'broken_profile')
    caplog.set_level(logging.DEBUG, logger=profile_module.logger.name)

    class BrokenDict(dict):
        def __contains__(self, key):
            raise RuntimeError('broken contains')

    manager.profile = BrokenDict()

    assert manager.get_profile_setting('language', 'en') == 'en'
    assert 'get_profile_setting fallback for language' in caplog.text
