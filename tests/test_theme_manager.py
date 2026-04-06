from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from src.audio.audio_events import AudioEventType
from src.model.theme_manager import ThemeManager
import src.model.theme_manager as theme_manager_module


RUNTIME_ROOT = Path(__file__).resolve().parent / '_theme_manager_runtime'


class DummyLM:
    def __init__(self, mapping=None, fail=False):
        self.mapping = mapping or {}
        self.fail = fail

    def get_text(self, key, **kwargs):
        if self.fail:
            raise ValueError('boom')
        text = self.mapping.get(key, key)
        return text.format(**kwargs) if kwargs else text


class DummyBus:
    def __init__(self):
        self.calls = []

    def publish(self, event_type, payload):
        self.calls.append((event_type, payload))



def _make_manager(monkeypatch, subdir: str, lm=None, bus=None, builtins=None):
    runtime_dir = RUNTIME_ROOT / subdir
    shutil.rmtree(runtime_dir, ignore_errors=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(theme_manager_module, 'get_app_data_path', lambda: runtime_dir)
    if builtins is None:
        builtins = {
            'dark': {'bg_color': '#111111', 'name': 'dark'},
            'light': {'bg_color': '#ffffff', 'name': 'light'},
        }
    monkeypatch.setattr(theme_manager_module, 'get_builtin_themes', lambda: dict(builtins))
    monkeypatch.setattr(theme_manager_module, 'BUILTIN_COLOR_THEME_NAMES', ['blue', 'green'])
    return ThemeManager(localization_manager=lm, event_bus=bus)



def test_get_localized_text_falls_back_cleanly(monkeypatch, caplog):
    manager = _make_manager(monkeypatch, 'localized', lm=DummyLM({'theme_manager_initialized': 'ok'}))
    assert manager._get_localized_text('theme_manager_initialized') == 'ok'

    caplog.set_level(logging.DEBUG, logger=theme_manager_module.logger.name)
    broken = _make_manager(monkeypatch, 'localized_broken', lm=DummyLM(fail=True))
    assert broken._get_localized_text('missing', value='x') == '[missing]'
    assert 'ThemeManager localization fallback for missing' in caplog.text



def test_load_custom_themes_merges_json(monkeypatch):
    runtime_dir = RUNTIME_ROOT / 'load_custom'
    shutil.rmtree(runtime_dir, ignore_errors=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / 'custom_themes.json').write_text(json.dumps({'pink': {'bg_color': '#ff00ff'}}), encoding='utf-8')
    monkeypatch.setattr(theme_manager_module, 'get_app_data_path', lambda: runtime_dir)
    monkeypatch.setattr(theme_manager_module, 'get_builtin_themes', lambda: {'dark': {'bg_color': '#111111'}})
    monkeypatch.setattr(theme_manager_module, 'BUILTIN_COLOR_THEME_NAMES', ['blue'])

    manager = ThemeManager()

    assert manager.themes['pink']['bg_color'] == '#ff00ff'



def test_set_theme_publishes_and_handles_no_change(monkeypatch):
    bus = DummyBus()
    manager = _make_manager(monkeypatch, 'set_theme', bus=bus)

    assert manager.set_theme('dark', 'blue') is False
    assert manager.set_theme('light', 'green') is True
    assert manager.mode == 'light'
    assert manager.color_theme == 'green'
    assert bus.calls[-1] == (AudioEventType.THEME_CHANGED, {'mode': 'light', 'color_theme': 'green'})



def test_system_theme_falls_back_to_default_mode(monkeypatch):
    manager = _make_manager(monkeypatch, 'system_mode')
    manager.mode = 'system'
    manager.default_mode = 'light'

    assert manager.get_current_theme_colors()['bg_color'] == '#ffffff'



def test_custom_theme_save_and_reset(monkeypatch):
    manager = _make_manager(monkeypatch, 'custom_theme')

    manager.set_custom_theme_color('bg_color', '#222222')
    saved = json.loads(manager.custom_themes_file.read_text(encoding='utf-8'))
    assert saved['custom']['bg_color'] == '#222222'
    assert manager.mode == 'custom'

    manager.reset_custom_theme_colors()
    assert manager.themes['custom']['name'] == 'custom'
    assert manager.mode == 'custom'



def test_callbacks_register_notify_unregister(monkeypatch):
    manager = _make_manager(monkeypatch, 'callbacks', lm=DummyLM({
        'registered_theme_callback': '{callback}',
        'unregistered_theme_callback': '{callback}',
        'notifying_theme_change': '{count}',
        'error_in_theme_callback': '{callback}:{error}',
        'theme_manager_initialized': '{theme}:{color}',
    }))
    calls = []

    def good():
        calls.append('good')

    def bad():
        raise RuntimeError('bad')

    manager.register_theme_change_callback(good)
    manager.register_theme_change_callback(bad)
    manager.notify_theme_change()
    manager.unregister_theme_change_callback(good)

    assert calls == ['good']
    assert good not in manager._theme_change_callbacks



def test_close_clears_callbacks(monkeypatch):
    manager = _make_manager(monkeypatch, 'close_case')
    manager.register_theme_change_callback(lambda: None)
    manager.close()
    assert manager._theme_change_callbacks == []
