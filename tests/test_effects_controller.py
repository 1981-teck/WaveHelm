from __future__ import annotations

from src.audio.audio_events import AudioEventType
from src.controller.effects_controller import EffectState, EffectsController
from src.utils.exceptions import ProfileError, ValidationError


class DummyEffectsEngine:
    def __init__(self):
        self.enabled = {}
        self.settings = {"echo": {"enabled": False}, "reverb": {"enabled": False}}
        self.raise_on = {}
        self.calls = []
        self.applied_settings = []

    def is_effect_enabled(self, effect_name):
        error = self.raise_on.get('is_effect_enabled')
        if error:
            raise error
        return self.enabled.get(effect_name, False)

    def get_effect_settings(self, effect_name):
        error = self.raise_on.get('get_effect_settings')
        if error:
            raise error
        return dict(self.settings.get(effect_name, {}))

    def get_current_settings(self):
        error = self.raise_on.get('get_current_settings')
        if error:
            raise error
        return dict(self.settings)

    def set_effect_enabled(self, effect_name, enabled):
        error = self.raise_on.get('set_effect_enabled')
        if error:
            raise error
        self.calls.append(('set_effect_enabled', effect_name, enabled))
        self.enabled[effect_name] = enabled

    def set_effect_parameter(self, effect_name, param_name, value):
        error = self.raise_on.get('set_effect_parameter')
        if error:
            raise error
        self.calls.append(('set_effect_parameter', effect_name, param_name, value))

    def reset_effect(self, effect_name):
        error = self.raise_on.get('reset_effect')
        if error:
            raise error
        self.calls.append(('reset_effect', effect_name))

    def reset_all_effects(self):
        error = self.raise_on.get('reset_all_effects')
        if error:
            raise error
        self.calls.append(('reset_all_effects',))

    def apply_settings(self, settings):
        error = self.raise_on.get('apply_settings')
        if error:
            raise error
        self.applied_settings.append(settings)
        self.settings = dict(settings)


class DummyProfileManager:
    def __init__(self, fail=False, settings=None):
        self.fail = fail
        self.saved = []
        self.settings = dict(settings or {})

    def set_effects_settings(self, settings):
        if self.fail:
            raise ProfileError('profile failed')
        self.saved.append(settings)
        self.settings = dict(settings)

    def get_effects_settings(self):
        if self.fail:
            raise ProfileError('profile failed')
        return dict(self.settings)


class DummyLocalization:
    def __init__(self, mapping=None, fail=False):
        self.mapping = mapping or {}
        self.fail = fail

    def get_text(self, key, default=None):
        if self.fail:
            raise ValueError('loc fail')
        return self.mapping.get(key, default or key)


class DummyEventBus:
    def __init__(self, fail_publish=False, fail_subscribe=False, fail_unsubscribe=False):
        self.fail_publish = fail_publish
        self.fail_subscribe = fail_subscribe
        self.fail_unsubscribe = fail_unsubscribe
        self.published = []
        self.subscribed = []
        self.unsubscribed = []

    def publish(self, event_type, payload):
        if self.fail_publish:
            raise ValueError('publish fail')
        self.published.append((event_type, payload))

    def subscribe(self, event_type, callback):
        if self.fail_subscribe:
            raise RuntimeError('subscribe fail')
        self.subscribed.append((event_type, callback))

    def unsubscribe(self, event_type, callback=None):
        if self.fail_unsubscribe:
            raise RuntimeError('unsubscribe fail')
        self.unsubscribed.append((event_type, callback))


class DummyVideoPlayer:
    def __init__(self, fail=False):
        self.fail = fail
        self.received = []

    def set_effects(self, settings):
        if self.fail:
            raise ValueError('video fail')
        self.received.append(settings)


def _make_controller(**kwargs):
    engine = kwargs.pop('engine', DummyEffectsEngine())
    event_bus = kwargs.pop('event_bus', DummyEventBus())
    localization = kwargs.pop('localization', DummyLocalization({
        'effects_controller_initialized': 'init',
        'enabled': 'enabled',
        'disabled': 'disabled',
        'effects_toggled_feedback': '{name}:{status}',
        'effects_param_set_feedback': '{effect}:{param}:{value}',
        'effects_settings_saved': 'saved',
        'effect_reset_feedback': 'reset {name}',
        'effects_all_reset_feedback': 'reset all',
        'effects_validation_error': '{name}:{error}',
        'operation_failed': 'failed {details}',
        'effects_set_enabled_error': 'enable error {effect}',
        'effects_set_param_error': 'param error {effect}:{param}',
        'effects_invalid_effect_name': 'invalid {name}',
        'error_saving_effects_settings_to_profile': 'save error',
        'effects_reset_error': 'reset error {effect}',
        'effects_reset_all_error': 'reset all error',
        'unexpected_error': 'unexpected',
    }))
    controller = EffectsController(
        effects_engine=engine,
        event_bus=event_bus,
        localization_manager=localization,
        **kwargs,
    )
    return controller, engine, event_bus


def test_get_effect_state_uses_engine_and_falls_back_to_disabled():
    controller, engine, _ = _make_controller()
    engine.enabled['echo'] = True
    assert controller.get_effect_state('echo') is EffectState.ENABLED

    engine.raise_on['is_effect_enabled'] = ValidationError('bad')
    assert controller.get_effect_state('echo') is EffectState.DISABLED


def test_localized_text_falls_back_cleanly_when_localization_or_format_fails():
    controller, _, _ = _make_controller(localization=DummyLocalization({'hello': 'ciao {name}'}))
    assert controller._get_localized_text('hello', name='Marco') == 'ciao Marco'
    assert controller._get_localized_text('hello') == 'ciao {name}'

    broken, _, _ = _make_controller(localization=DummyLocalization(fail=True))
    assert broken._get_localized_text('missing', default='fallback') == 'fallback'


def test_effect_setters_and_reset_publish_feedback_and_route_errors():
    controller, engine, event_bus = _make_controller()

    controller.set_effect_enabled('echo', True)
    controller.set_effect_parameter('echo', 'delay', 0.5)
    controller.reset_effect('echo')
    controller.reset_all_effects()

    published_feedback = [payload['message'] for event_type, payload in event_bus.published if event_type == AudioEventType.FEEDBACK_MESSAGE]
    assert 'echo:enabled' in published_feedback
    assert 'echo:delay:0.5' in published_feedback
    assert 'reset echo' in published_feedback
    assert 'reset all' in published_feedback

    engine.raise_on['set_effect_enabled'] = ValidationError('bad enable')
    controller.set_effect_enabled('reverb', False)
    assert any(event_type == AudioEventType.ERROR for event_type, _ in event_bus.published)


def test_get_effect_settings_and_save_current_settings_to_profile():
    profile = DummyProfileManager()
    controller, engine, event_bus = _make_controller(profile_manager=profile)

    assert controller.get_effect_settings('echo') == {'enabled': False}

    engine.raise_on['get_effect_settings'] = ValueError('bad effect')
    assert controller.get_effect_settings('missing') == {}

    assert controller.save_current_settings_to_profile() is True
    assert profile.saved[-1] == engine.settings
    assert event_bus.published[-1][1]['message'] == 'saved'

    failing_controller, failing_engine, failing_bus = _make_controller(profile_manager=DummyProfileManager(fail=True))
    assert failing_controller.save_current_settings_to_profile() is False
    assert any(event_type == AudioEventType.ERROR for event_type, _ in failing_bus.published)

    no_profile_controller, _, no_profile_bus = _make_controller(profile_manager=None)
    assert no_profile_controller.save_current_settings_to_profile() is False
    assert any(event_type == AudioEventType.ERROR for event_type, _ in no_profile_bus.published)


def test_controller_restores_saved_effects_settings_during_initialization():
    profile = DummyProfileManager(settings={'echo': {'enabled': True, 'delay_ms': 450.0}})
    controller, engine, _event_bus = _make_controller(profile_manager=profile)

    assert controller.profile_manager is profile
    assert engine.applied_settings == [{'echo': {'enabled': True, 'delay_ms': 450.0}}]
    assert engine.settings == {'echo': {'enabled': True, 'delay_ms': 450.0}}


def test_on_effects_changed_saves_profile_updates_video_player_and_handles_failures():
    profile = DummyProfileManager()
    video_player = DummyVideoPlayer()
    controller, _, event_bus = _make_controller(profile_manager=profile, video_player=video_player)

    controller._on_effects_changed({'settings': {'echo': {'enabled': True}}})
    assert profile.saved[-1] == {'echo': {'enabled': True}}
    assert video_player.received[-1] == {'echo': {'enabled': True}}

    failing_profile_controller, _, failing_profile_bus = _make_controller(profile_manager=DummyProfileManager(fail=True), video_player=DummyVideoPlayer())
    failing_profile_controller._on_effects_changed({'settings': {'echo': {'enabled': True}}})
    assert any(event_type == AudioEventType.ERROR for event_type, _ in failing_profile_bus.published)

    failing_video_controller, _, failing_video_bus = _make_controller(profile_manager=DummyProfileManager(), video_player=DummyVideoPlayer(fail=True))
    failing_video_controller._on_effects_changed({'settings': {'echo': {'enabled': True}}})
    assert any(event_type == AudioEventType.ERROR for event_type, _ in failing_video_bus.published)


def test_close_and_event_bus_failures_remain_best_effort():
    controller, _, event_bus = _make_controller(event_bus=DummyEventBus())
    assert event_bus.subscribed and event_bus.subscribed[0][0] == AudioEventType.EFFECTS_CHANGED
    controller.close()
    assert event_bus.unsubscribed and event_bus.unsubscribed[0][0] == AudioEventType.EFFECTS_CHANGED

    failing_bus = DummyEventBus(fail_publish=True, fail_subscribe=True, fail_unsubscribe=True)
    controller = EffectsController(DummyEffectsEngine(), event_bus=failing_bus, localization_manager=DummyLocalization())
    controller._notify_feedback('anything')
    controller.close()
