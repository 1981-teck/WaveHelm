from __future__ import annotations

import numpy as np
import pytest

from src.audio.audio_events import AudioEventType
from src.audio.equalizer import Equalizer


class DummyEventBus:
    def __init__(self):
        self.subscriptions = []
        self.published = []

    def subscribe(self, event_type, callback):
        self.subscriptions.append((event_type, callback))

    def publish(self, event_type, payload):
        self.published.append((event_type, payload))


class DummyLocalization:
    def get_text(self, key: str, **kwargs):
        return key


class DummySettings:
    def __init__(self):
        self.values = {'equalizer_enabled': True, 'eq_last_preset': 'Flat'}

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value


class DummyDbManager:
    def __init__(self, *, fail_get=False, fail_save=False, fail_delete=False):
        self.fail_get = fail_get
        self.fail_save = fail_save
        self.fail_delete = fail_delete
        self.saved = {}

    def get_custom_eq_presets(self):
        if self.fail_get:
            raise RuntimeError('load fail')
        return [{'name': name, 'settings': settings} for name, settings in self.saved.items()]

    def add_custom_eq_preset(self, name, settings):
        if self.fail_save:
            raise RuntimeError('save fail')
        self.saved[name] = settings.copy()

    def delete_custom_eq_preset(self, name):
        if self.fail_delete:
            raise RuntimeError('delete fail')
        self.saved.pop(name, None)


def test_equalizer_load_failure_falls_back_to_empty_custom_presets():
    eq = Equalizer(DummyDbManager(fail_get=True), DummyLocalization(), DummyEventBus(), DummySettings())
    assert eq.custom_presets == {}


def test_equalizer_save_and_delete_custom_preset_flow():
    bus = DummyEventBus()
    db = DummyDbManager()
    eq = Equalizer(db, DummyLocalization(), bus, DummySettings())

    eq.set_band_gain('31Hz', 4.0)
    assert eq.save_custom_preset('MyPreset') is True
    assert 'MyPreset' in db.saved
    assert bus.published[-1][0] == AudioEventType.CUSTOM_PRESETS_UPDATED

    assert eq.delete_custom_preset('MyPreset') is True
    assert 'MyPreset' not in db.saved

    with pytest.raises(ValueError):
        eq.delete_custom_preset('Flat')




def test_equalizer_saved_custom_preset_becomes_current_but_close_resets_startup_state_to_flat():
    bus = DummyEventBus()
    settings = DummySettings()
    db = DummyDbManager()
    eq = Equalizer(db, DummyLocalization(), bus, settings)

    eq.set_band_gain('31Hz', 4.0)
    eq.set_band_gain('62Hz', -2.5)

    assert eq.save_custom_preset('Night Drive') is True
    assert eq.get_current_preset_name() == 'Night Drive'
    assert eq.custom_presets['Night Drive']['31Hz'] == 4.0
    assert eq.custom_presets['Night Drive']['62Hz'] == -2.5

    eq.close()

    assert settings.values['eq_last_preset'] == 'Flat'
    assert settings.values['eq_last_gains'] == Equalizer.PREDEFINED_PRESETS['Flat']




def test_equalizer_deleting_active_custom_preset_falls_back_to_flat_and_persists_flat():
    settings = DummySettings()
    db = DummyDbManager()
    eq = Equalizer(db, DummyLocalization(), DummyEventBus(), settings)

    eq.set_band_gain('31Hz', 5.0)
    eq.set_band_gain('125Hz', -3.0)
    assert eq.save_custom_preset('Night Drive') is True
    assert eq.get_current_preset_name() == 'Night Drive'

    assert eq.delete_custom_preset('Night Drive') is True
    assert eq.get_current_preset_name() == 'Flat'
    assert eq.get_band_gain('31Hz') == 0.0
    assert eq.get_band_gain('125Hz') == 0.0
    assert 'Night Drive' not in eq.custom_presets
    assert 'Night Drive' not in db.saved

    eq.close()

    assert settings.values['eq_last_preset'] == 'Flat'
    assert settings.values['eq_last_gains'] == Equalizer.PREDEFINED_PRESETS['Flat']

def test_equalizer_rejects_builtin_and_case_insensitive_duplicate_custom_names():
    eq = Equalizer(DummyDbManager(), DummyLocalization(), DummyEventBus(), DummySettings())

    eq.set_band_gain('31Hz', 3.0)
    assert eq.save_custom_preset('Night Drive') is True

    with pytest.raises(ValueError):
        eq.save_custom_preset('Flat')

    with pytest.raises(ValueError):
        eq.save_custom_preset('night drive')

def test_equalizer_restore_missing_preset_uses_custom_runtime_but_close_resets_startup_state():
    settings = DummySettings()
    settings.values['eq_last_preset'] = 'Night Drive'
    settings.values['eq_last_gains'] = {'31Hz': 4.5, '62Hz': -99.0, '1kHz': 2.0}

    eq = Equalizer(DummyDbManager(), DummyLocalization(), DummyEventBus(), settings)

    assert eq.get_current_preset_name() == 'Custom'
    assert eq.get_band_gain('31Hz') == 4.5
    assert eq.get_band_gain('62Hz') == -12.0
    assert eq.get_band_gain('1kHz') == 2.0
    assert eq.get_band_gain('16kHz') == 0.0

    eq.close()

    assert settings.values['eq_last_preset'] == 'Flat'
    assert settings.values['eq_last_gains'] == Equalizer.PREDEFINED_PRESETS['Flat']


def test_equalizer_apply_and_validate_settings():
    eq = Equalizer(DummyDbManager(), DummyLocalization(), DummyEventBus(), DummySettings())

    assert eq.apply_preset('Missing') is False
    eq.set_eq_settings({'enabled': True, 'band_gains': {'31Hz': 99, '62Hz': -99}, 'preset_name': 'CustomX'})
    assert eq.get_band_gain('31Hz') == 12.0
    assert eq.get_band_gain('62Hz') == -12.0
    assert eq.get_current_preset_name() == 'CustomX'

    with pytest.raises(ValueError):
        eq.set_eq_settings({'band_gains': {'31Hz': 'bad'}})

    audio = np.ones((4, 2), dtype=np.float32)
    processed = eq.apply_eq_to_chunk(audio)
    assert processed.shape == audio.shape
