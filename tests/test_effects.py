from __future__ import annotations

import numpy as np

from src.audio.audio_events import AudioEventType
from src.audio.effects import EffectsEngine
import src.audio.effects as effects_module


class DummyEventBus:
    def __init__(self):
        self.subscriptions = []
        self.unsubscriptions = []
        self.published = []

    def subscribe(self, event_type, callback):
        self.subscriptions.append((event_type, callback))

    def unsubscribe(self, event_type, callback=None):
        self.unsubscriptions.append((event_type, callback))

    def publish(self, event_type, payload):
        self.published.append((event_type, payload))


class DummyLocalization:
    def __init__(self, values=None):
        self.values = values or {}

    def get_text(self, key, default=None):
        return self.values.get(key, default if default is not None else key)


class DummySettings:
    pass


def test_get_localized_text_falls_back_when_formatting_fails():
    engine = EffectsEngine(DummyEventBus(), DummyLocalization({'broken': '{missing}'}), DummySettings())
    assert engine._get_localized_text('broken', value='x') == '{missing}'


def test_apply_effects_returns_original_and_publishes_error_on_runtime_failure(monkeypatch):
    bus = DummyEventBus()
    engine = EffectsEngine(bus, DummyLocalization(), DummySettings())
    engine.set_effect_enabled('echo', True)
    monkeypatch.setattr(effects_module, 'apply_echo', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('boom')))

    audio = np.ones((8, 2), dtype=np.float32)
    processed = engine.apply_effects(audio)

    assert np.array_equal(processed, audio)
    assert bus.published[-1][0] == AudioEventType.ERROR


def test_apply_effects_returns_original_and_publishes_error_on_missing_dsp(monkeypatch):
    bus = DummyEventBus()
    engine = EffectsEngine(bus, DummyLocalization(), DummySettings())
    engine.set_effect_enabled('reverb', True)
    monkeypatch.setattr(effects_module, 'apply_reverb', lambda *args, **kwargs: (_ for _ in ()).throw(NameError('missing')))

    audio = np.ones((8, 2), dtype=np.float32)
    processed = engine.apply_effects(audio)

    assert np.array_equal(processed, audio)
    assert bus.published[-1][0] == AudioEventType.ERROR


def test_set_effect_parameter_updates_state_and_close_unsubscribes():
    bus = DummyEventBus()
    engine = EffectsEngine(bus, DummyLocalization(), DummySettings())

    engine.set_effect_parameter('echo', 'delay_ms', 120)
    engine.set_effect_parameter('reverb', 'wet_level', 0.7)

    settings = engine.get_current_settings()
    assert settings['echo']['delay_ms'] == 120
    assert settings['reverb']['wet_level'] == 0.7
    assert bus.published[-1][0] == AudioEventType.EFFECTS_CHANGED

    engine.close()
    assert len(bus.unsubscriptions) == 2
