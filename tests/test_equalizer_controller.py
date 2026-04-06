from __future__ import annotations

from src.audio.audio_events import AudioEventType
from src.controller.equalizer_controller import EqualizerController
from src.utils.exceptions import DatabaseError, NotFoundError, ProfileError, ValidationError


class DummyEqualizer:
    def __init__(self):
        self.is_enabled = False
        self.raise_on = {}
        self.calls = []
        self.state = {'band_gains': {'60': 0.0}, 'current_preset': 'Flat'}
        self._builtin_presets = ['Flat', 'Rock']
        self._custom_presets = []

    def set_band_gain(self, band_name, gain_db):
        error = self.raise_on.get('set_band_gain')
        if error:
            raise error
        self.calls.append(('set_band_gain', band_name, gain_db))
        self.state['band_gains'][band_name] = gain_db
        self.state['current_preset'] = 'Custom'

    def get_all_bands(self):
        return {'60': {'frequency': 60, 'gain': 0.0}}

    def enable(self, enable=None):
        error = self.raise_on.get('enable')
        if error:
            raise error
        self.calls.append(('enable', enable))
        self.is_enabled = not self.is_enabled if enable is None else bool(enable)

    def set_preset(self, preset_name):
        error = self.raise_on.get('set_preset')
        if error:
            raise error
        self.calls.append(('set_preset', preset_name))
        self.state['current_preset'] = preset_name

    def save_custom_preset(self, preset_name):
        error = self.raise_on.get('save_custom_preset')
        if error:
            raise error
        self.calls.append(('save_custom_preset', preset_name))
        if preset_name not in self._custom_presets:
            self._custom_presets.append(preset_name)
        self.state['current_preset'] = preset_name

    def delete_custom_preset(self, preset_name):
        error = self.raise_on.get('delete_custom_preset')
        if error:
            raise error
        self.calls.append(('delete_custom_preset', preset_name))
        if preset_name in self._builtin_presets:
            raise ValidationError('built-in preset cannot be deleted')
        if preset_name not in self._custom_presets:
            return False
        self._custom_presets.remove(preset_name)
        self.state['current_preset'] = 'Flat'
        return True

    def get_available_presets(self):
        return self._builtin_presets + self._custom_presets

    def get_current_state(self):
        return dict(self.state)


class DummyProfileManager:
    def __init__(self, *, fail_set=False, fail_increment=False):
        self.fail_set = fail_set
        self.fail_increment = fail_increment
        self.eq_settings = []
        self.stats = []

    def set_eq_settings(self, data):
        if self.fail_set:
            raise ProfileError('profile fail')
        self.eq_settings.append(data)

    def increment_stat(self, name):
        if self.fail_increment:
            raise DatabaseError('stat fail')
        self.stats.append(name)


class DummyLocalization:
    def __init__(self, mapping=None, fail=False):
        self.mapping = mapping or {}
        self.fail = fail

    def get_text(self, key, default=None):
        if self.fail:
            raise ValueError('loc fail')
        return self.mapping.get(key, default or key)


class DummyEventBus:
    def __init__(self, *, fail_publish=False, fail_subscribe=False, fail_unsubscribe=False):
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


def _make_controller(**kwargs):
    equalizer = kwargs.pop('equalizer', DummyEqualizer())
    profile_manager = kwargs.pop('profile_manager', DummyProfileManager())
    localization = kwargs.pop('localization_manager', DummyLocalization({
        'equalizer_controller_initialized': 'init',
        'enabled': 'enabled',
        'disabled': 'disabled',
        'eq_band_changed_feedback': '{band}:{gain}',
        'equalizer_toggled': '{status}',
        'eq_preset_applied_feedback': '{preset}',
        'eq_new_preset_saved': 'saved {name}',
        'eq_preset_not_found_delete': 'missing {name}',
        'eq_preset_deleted': 'deleted {name}',
        'operation_failed': 'failed {details}',
        'eq_set_band_error': 'band error {band}',
        'error_toggling_equalizer': 'toggle error',
        'eq_error_applying_preset': 'preset error {preset}',
        'eq_error_saving_preset': 'save preset error {name}',
        'eq_error_deleting_preset': 'delete preset error {name}',
        'error_saving_eq_settings_to_profile': 'profile save error',
        'unexpected_error': 'unexpected',
    }))
    event_bus = kwargs.pop('event_bus', DummyEventBus())
    controller = EqualizerController(equalizer, profile_manager, localization, event_bus)
    return controller, equalizer, profile_manager, event_bus


def test_localized_text_falls_back_on_localization_or_format_failure():
    controller, _, _, _ = _make_controller(localization_manager=DummyLocalization({'hello': 'ciao {name}'}))
    assert controller._get_localized_text('hello', name='Marco') == 'ciao Marco'
    assert controller._get_localized_text('hello') == 'ciao {name}'

    broken, _, _, _ = _make_controller(localization_manager=DummyLocalization(fail=True))
    assert broken._get_localized_text('missing', default='fallback') == 'fallback'


def test_band_toggle_and_preset_actions_publish_feedback_and_route_errors():
    controller, equalizer, _, event_bus = _make_controller()
    equalizer.save_custom_preset('UserA')
    equalizer.calls.clear()

    controller.set_band_gain('60', 3.0)
    controller.reset_band_to_default('60')
    controller.toggle_equalizer(True)
    controller.apply_preset('Rock')
    controller.delete_custom_preset('missing')
    controller.delete_custom_preset('UserA')

    feedback_messages = [payload['message'] for event_type, payload in event_bus.published if event_type == AudioEventType.FEEDBACK_MESSAGE]
    assert '60:3.0' not in feedback_messages
    assert '60:0.0' not in feedback_messages
    assert 'enabled' in feedback_messages
    assert 'Rock' in feedback_messages
    assert 'missing missing' in feedback_messages
    assert 'deleted UserA' in feedback_messages

    equalizer.raise_on['set_band_gain'] = ValidationError('bad gain')
    controller.set_band_gain('125', 6.0)
    assert any(event_type == AudioEventType.ERROR for event_type, _ in event_bus.published)



def test_set_band_gain_updates_engine_without_per_tick_feedback():
    controller, equalizer, _, event_bus = _make_controller()

    controller.set_band_gain('60', 5.0)

    assert ('set_band_gain', '60', 5.0) in equalizer.calls
    feedback_messages = [payload['message'] for event_type, payload in event_bus.published if event_type == AudioEventType.FEEDBACK_MESSAGE]
    assert '60:5.0' not in feedback_messages


def test_save_custom_preset_and_profile_sync_behaviour():
    controller, equalizer, profile_manager, event_bus = _make_controller()

    controller.save_current_as_custom_preset('MyPreset')
    assert ('save_custom_preset', 'MyPreset') in equalizer.calls
    assert profile_manager.stats == ['eq_presets_saved']
    assert event_bus.published[-1][1]['message'] == 'saved MyPreset'

    non_blocking_controller, _, non_blocking_profile, non_blocking_bus = _make_controller(profile_manager=DummyProfileManager(fail_increment=True))
    non_blocking_controller.save_current_as_custom_preset('StatFail')
    assert any(event_type == AudioEventType.FEEDBACK_MESSAGE and payload['message'] == 'saved StatFail' for event_type, payload in non_blocking_bus.published)
    assert non_blocking_profile.stats == []

    failing_equalizer = DummyEqualizer()
    failing_equalizer.raise_on['save_custom_preset'] = NotFoundError('cannot save')
    failing_controller, _, _, failing_bus = _make_controller(equalizer=failing_equalizer)
    failing_controller.save_current_as_custom_preset('Broken')
    assert any(event_type == AudioEventType.ERROR for event_type, _ in failing_bus.published)


def test_query_helpers_and_eq_changed_profile_save():
    controller, equalizer, profile_manager, event_bus = _make_controller()
    assert controller.get_all_bands() == {'60': {'frequency': 60, 'gain': 0.0}}
    assert controller.get_all_preset_names() == ['Flat', 'Rock']
    assert controller.get_current_state() == {'band_gains': {'60': 0.0}, 'current_preset': 'Flat'}

    payload = {'band_gains': {'60': 2.0}}
    controller._on_eq_changed(payload)
    assert profile_manager.eq_settings[-1] == payload

    failing_controller, _, _, failing_bus = _make_controller(profile_manager=DummyProfileManager(fail_set=True))
    failing_controller._on_eq_changed(payload)
    assert any(event_type == AudioEventType.ERROR for event_type, _ in failing_bus.published)


def test_controller_surfaces_saved_preset_in_queries_and_current_state():
    controller, _, _, _ = _make_controller()

    controller.save_current_as_custom_preset('Night Drive')

    assert controller.get_all_preset_names() == ['Flat', 'Rock', 'Night Drive']
    assert controller.get_current_state()['current_preset'] == 'Night Drive'




def test_controller_rejects_missing_preset_without_false_success_feedback():
    controller, equalizer, _, event_bus = _make_controller()

    controller.apply_preset('MissingPreset')

    error_messages = [payload['message'] for event_type, payload in event_bus.published if event_type == AudioEventType.ERROR]
    feedback_messages = [payload['message'] for event_type, payload in event_bus.published if event_type == AudioEventType.FEEDBACK_MESSAGE]
    assert any('preset error MissingPreset' in message for message in error_messages)
    assert not any(message == 'MissingPreset' for message in feedback_messages)
    assert ('set_preset', 'MissingPreset') not in equalizer.calls


def test_controller_rejects_blank_preset_name_without_touching_engine():
    controller, equalizer, _, event_bus = _make_controller()

    controller.apply_preset('   ')

    error_messages = [payload['message'] for event_type, payload in event_bus.published if event_type == AudioEventType.ERROR]
    feedback_messages = [payload['message'] for event_type, payload in event_bus.published if event_type == AudioEventType.FEEDBACK_MESSAGE]
    assert any('preset error' in message for message in error_messages)
    assert not any(payload.get('message') == '' for _, payload in event_bus.published if isinstance(payload, dict))
    assert not feedback_messages or all(message != '' for message in feedback_messages)
    assert not any(call[0] == 'set_preset' for call in equalizer.calls)


def test_controller_reports_error_when_built_in_delete_is_rejected():
    controller, _, _, event_bus = _make_controller()

    controller.delete_custom_preset('Flat')

    error_messages = [payload['message'] for event_type, payload in event_bus.published if event_type == AudioEventType.ERROR]
    feedback_messages = [payload['message'] for event_type, payload in event_bus.published if event_type == AudioEventType.FEEDBACK_MESSAGE]
    assert any('delete preset error Flat' in message for message in error_messages)
    assert not any(message == 'deleted Flat' for message in feedback_messages)


def test_close_and_event_bus_failures_are_best_effort():
    controller, _, _, event_bus = _make_controller()
    assert event_bus.subscribed and event_bus.subscribed[0][0] == AudioEventType.EQ_CHANGED
    controller.close()
    assert event_bus.unsubscribed and event_bus.unsubscribed[0][0] == AudioEventType.EQ_CHANGED

    broken = EqualizerController(DummyEqualizer(), DummyProfileManager(), DummyLocalization(), DummyEventBus(fail_publish=True, fail_subscribe=True, fail_unsubscribe=True))
    broken._notify_feedback('anything')
    broken.close()
