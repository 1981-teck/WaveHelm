from __future__ import annotations

import sys

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.equalizer_view import EqualizerView
from tests.wx_fakes import FakeTextEntryDialog, FakeWxModule


class DummyLocalizationManager:
    def __init__(self) -> None:
        self.language_callbacks = []

    def register_language_change_callback(self, callback):
        self.language_callbacks.append(callback)

    def unregister_language_change_callback(self, callback):
        if callback in self.language_callbacks:
            self.language_callbacks.remove(callback)

    def get_text(self, key, default=None, **kwargs):
        mapping = {
            'nav_equalizer': 'Equalizzatore',
            'equalizer_title': 'Equalizzatore',
            'equalizer_enable': 'Attiva EQ',
            'equalizer_disable': 'Disattiva EQ',
            'equalizer_reset': 'Reset EQ',
            'equalizer_state_enabled': 'EQ attivo.',
            'equalizer_state_disabled': 'EQ disattivato.',
            'profile_effects_setting_name_placeholder': 'Nome preset',
            'profile_confirm_delete_effects_setting_message': 'Eliminare {name}?',
            'profile_confirm_delete_effects_setting_title': 'Conferma eliminazione',
            'profile_save_effects_setting_button': 'Salva',
            'profile_delete_effects_setting_button': 'Elimina',
        }
        text = mapping.get(key, default or key)
        return text.format(**kwargs) if kwargs else text


class DummyThemeManager:
    def __init__(self) -> None:
        self.theme_callbacks = []

    def register_theme_change_callback(self, callback):
        self.theme_callbacks.append(callback)

    def unregister_theme_change_callback(self, callback):
        if callback in self.theme_callbacks:
            self.theme_callbacks.remove(callback)

    def get_current_theme_colors(self):
        return {
            'bg_color': '#101010',
            'panel_bg': '#181818',
            'text_color': '#f5f5f5',
            'button_color': '#242424',
        }


class DummyEventBus:
    def __init__(self) -> None:
        self.subscriptions = {}
        self.published = []

    def subscribe(self, event_type, callback):
        self.subscriptions.setdefault(event_type, []).append(callback)
        return callback

    def unsubscribe(self, event_type, subscription=None):
        callbacks = self.subscriptions.get(event_type, [])
        if subscription in callbacks:
            callbacks.remove(subscription)
            return True
        return False

    def publish(self, event_type, payload):
        self.published.append((event_type, payload))
        for callback in list(self.subscriptions.get(event_type, [])):
            callback(payload)


class DummyEqualizerController:
    def __init__(self) -> None:
        self.bands = {
            'bass': {'freq': 170, 'gain': 0.0},
            'mid': {'freq': 1000, 'gain': 0.0},
            'treble': {'freq': 8000, 'gain': -2.0},
        }
        self.state = {
            'enabled': True,
            'preset_name': 'Flat',
            'band_gains': {'bass': 0.0, 'mid': 0.0, 'treble': -2.0},
        }
        self.preset_names = ['Flat', 'Rock']
        self.calls = []

    def get_all_bands(self):
        return self.bands

    def get_current_state(self):
        return self.state

    def get_all_preset_names(self):
        return list(self.preset_names)

    def set_band_gain(self, band_name, gain):
        self.calls.append(('set_band_gain', band_name, round(float(gain), 1)))
        self.state['band_gains'][band_name] = float(gain)
        self.state['preset_name'] = 'Custom'

    def toggle_equalizer(self, enable=None):
        self.calls.append(('toggle_equalizer', bool(enable)))
        self.state['enabled'] = bool(enable)

    def apply_preset(self, preset_name):
        self.calls.append(('apply_preset', preset_name))
        self.state['preset_name'] = str(preset_name)
        if preset_name == 'Rock':
            self.state['band_gains'] = {'bass': 4.0, 'mid': 1.0, 'treble': 3.0}
        else:
            self.state['band_gains'] = {'bass': 0.0, 'mid': 0.0, 'treble': 0.0}

    def save_current_as_custom_preset(self, preset_name):
        self.calls.append(('save_preset', preset_name))
        if preset_name not in self.preset_names:
            self.preset_names.append(preset_name)
        self.state['preset_name'] = str(preset_name)
        self.state['band_gains'] = dict(self.state['band_gains'])

    def delete_custom_preset(self, preset_name):
        self.calls.append(('delete_preset', preset_name))
        if preset_name in self.preset_names and preset_name not in {'Flat', 'Rock'}:
            self.preset_names.remove(preset_name)
            self.state['preset_name'] = 'Flat'



def build_equalizer_view(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    localization = DummyLocalizationManager()
    theme = DummyThemeManager()
    event_bus = DummyEventBus()
    controller = DummyEqualizerController()
    view = EqualizerView(
        FakeWxModule.Panel(None),
        equalizer_controller=controller,
        localization_manager=localization,
        theme_manager=theme,
        event_bus=event_bus,
    )
    return view, controller, event_bus, localization, theme



def test_wx_equalizer_view_loads_state_and_localized_controls(monkeypatch):
    view, _controller, _event_bus, _localization, _theme = build_equalizer_view(monkeypatch)

    assert view.title_label.label == 'Equalizzatore'
    assert view.toggle_button.label == 'Disattiva EQ'
    assert view.status_label.label == 'EQ attivo.'
    assert view.preset_choice.items == ['Flat', 'Rock']
    assert view._band_value_labels['treble'].label == '-2.0 dB'



def test_wx_equalizer_view_dispatches_slider_toggle_and_presets(monkeypatch):
    view, controller, _event_bus, _localization, _theme = build_equalizer_view(monkeypatch)

    view._band_sliders['bass'].SetValue(180)
    view._band_sliders['bass'].trigger('EVT_SLIDER', None)
    view.toggle_button.click()
    view.preset_choice.SetStringSelection('Rock')
    view._on_preset_selected()

    FakeTextEntryDialog.next_value = 'Night'
    FakeTextEntryDialog.next_result = FakeWxModule.ID_OK
    view._on_save_preset()
    view.preset_choice.SetStringSelection('Night')
    FakeWxModule.next_message_box_result = FakeWxModule.YES
    view._on_delete_preset()

    assert ('set_band_gain', 'bass', 6.0) in controller.calls
    assert ('toggle_equalizer', False) in controller.calls
    assert ('apply_preset', 'Rock') in controller.calls
    assert ('save_preset', 'Night') in controller.calls
    assert ('delete_preset', 'Night') in controller.calls


def test_wx_equalizer_view_saved_custom_preset_stays_in_menu_with_defaults(monkeypatch):
    view, controller, _event_bus, _localization, _theme = build_equalizer_view(monkeypatch)

    view._band_sliders['bass'].SetValue(180)
    view._band_sliders['bass'].trigger('EVT_SLIDER', None)
    FakeTextEntryDialog.next_value = 'Night'
    FakeTextEntryDialog.next_result = FakeWxModule.ID_OK

    view._on_save_preset()

    assert ('save_preset', 'Night') in controller.calls
    assert view.preset_choice.items == ['Flat', 'Rock', 'Night']
    assert view.preset_choice.GetStringSelection() == 'Night'
    assert view.delete_preset_button.IsEnabled() is True



def test_wx_equalizer_view_prevents_builtin_preset_deletion_in_ui(monkeypatch):
    view, controller, _event_bus, _localization, _theme = build_equalizer_view(monkeypatch)
    message_box_calls = []

    def _message_box(*args, **kwargs):
        message_box_calls.append((args, kwargs))
        return FakeWxModule.YES

    monkeypatch.setattr(FakeWxModule, 'MessageBox', staticmethod(_message_box))

    assert view.preset_choice.GetStringSelection() == 'Flat'
    assert view.delete_preset_button.IsEnabled() is False

    view._on_delete_preset()

    assert ('delete_preset', 'Flat') not in controller.calls
    assert message_box_calls == []

    view.preset_choice.SetStringSelection('Rock')
    view._on_preset_selected()
    assert view.delete_preset_button.IsEnabled() is False

    view._on_delete_preset()

    assert ('delete_preset', 'Rock') not in controller.calls
    assert message_box_calls == []



def test_wx_equalizer_view_deleting_active_custom_preset_falls_back_to_flat(monkeypatch):
    view, controller, _event_bus, _localization, _theme = build_equalizer_view(monkeypatch)

    FakeTextEntryDialog.next_value = 'Night'
    FakeTextEntryDialog.next_result = FakeWxModule.ID_OK
    view._on_save_preset()
    assert view.preset_choice.GetStringSelection() == 'Night'
    assert view.delete_preset_button.IsEnabled() is True

    FakeWxModule.next_message_box_result = FakeWxModule.YES
    view._on_delete_preset()

    assert ('delete_preset', 'Night') in controller.calls
    assert 'Night' not in view.preset_choice.items
    assert view.preset_choice.items == ['Flat', 'Rock']
    assert view.preset_choice.GetStringSelection() == 'Flat'
    assert view.delete_preset_button.IsEnabled() is False


def test_wx_equalizer_view_slider_change_shows_custom_without_builtin_fallback(monkeypatch):
    view, controller, _event_bus, _localization, _theme = build_equalizer_view(monkeypatch)

    assert view.preset_choice.items == ['Flat', 'Rock']
    assert view.preset_choice.GetStringSelection() == 'Flat'
    assert view.delete_preset_button.IsEnabled() is False

    view._band_sliders['bass'].SetValue(180)
    view._band_sliders['bass'].trigger('EVT_SLIDER', None)

    assert ('set_band_gain', 'bass', 6.0) in controller.calls
    assert controller.get_current_state()['preset_name'] == 'Custom'
    assert view.preset_choice.items == ['Flat', 'Rock', 'Custom']
    assert view.preset_choice.GetStringSelection() == 'Custom'
    assert view.delete_preset_button.IsEnabled() is False


def test_wx_equalizer_view_refreshes_from_event_bus_and_shutdown(monkeypatch):
    view, _controller, event_bus, localization, theme = build_equalizer_view(monkeypatch)

    event_bus.publish(
        AudioEventType.EQ_CHANGED,
        {
            'enabled': False,
            'preset_name': 'Rock',
            'band_gains': {'bass': 4.0, 'mid': 1.0, 'treble': 3.0},
        },
    )
    event_bus.publish(AudioEventType.FEEDBACK_MESSAGE, {'message': 'Preset applied', 'color': 'green'})

    assert view.status_label.label == 'EQ disattivato.'
    assert view.feedback_label.label == 'Preset applied'
    assert view._band_value_labels['bass'].label == '4.0 dB'

    view.shutdown()

    assert localization.language_callbacks == []
    assert theme.theme_callbacks == []
    assert event_bus.subscriptions.get(AudioEventType.EQ_CHANGED) == []


def test_wx_equalizer_view_eq_changed_does_not_reautosize_toggle_when_enabled_state_is_unchanged(monkeypatch):
    view, _controller, event_bus, _localization, _theme = build_equalizer_view(monkeypatch)
    min_size_calls = []
    original_set_min_size = view.toggle_button.SetMinSize

    def _tracked_set_min_size(size):
        min_size_calls.append(tuple(size))
        original_set_min_size(size)

    view.toggle_button.SetMinSize = _tracked_set_min_size

    event_bus.publish(
        AudioEventType.EQ_CHANGED,
        {
            'enabled': True,
            'preset_name': 'Custom',
            'band_gains': {'bass': 3.0, 'mid': 0.0, 'treble': -2.0},
        },
    )
    event_bus.publish(
        AudioEventType.EQ_CHANGED,
        {
            'enabled': True,
            'preset_name': 'Custom',
            'band_gains': {'bass': 6.0, 'mid': 0.0, 'treble': -2.0},
        },
    )

    assert min_size_calls == []
    assert view.toggle_button.label == 'Disattiva EQ'



def test_wx_equalizer_view_band_value_labels_keep_stable_size_while_gains_change(monkeypatch):
    view, _controller, event_bus, _localization, _theme = build_equalizer_view(monkeypatch)

    assert view._band_value_labels['bass'].min_size == (72, 28)

    event_bus.publish(
        AudioEventType.EQ_CHANGED,
        {
            'enabled': True,
            'preset_name': 'Custom',
            'band_gains': {'bass': -12.0, 'mid': 0.0, 'treble': 12.0},
        },
    )
    first_size = view._band_value_labels['bass'].min_size

    event_bus.publish(
        AudioEventType.EQ_CHANGED,
        {
            'enabled': True,
            'preset_name': 'Custom',
            'band_gains': {'bass': 12.0, 'mid': 0.0, 'treble': -12.0},
        },
    )

    assert first_size == (72, 28)
    assert view._band_value_labels['bass'].min_size == (72, 28)
    assert view._band_value_labels['treble'].min_size == (72, 28)


def test_wx_equalizer_view_slider_change_does_not_reselect_custom_when_already_selected(monkeypatch):
    view, controller, _event_bus, _localization, _theme = build_equalizer_view(monkeypatch)

    view._band_sliders['bass'].SetValue(180)
    view._band_sliders['bass'].trigger('EVT_SLIDER', None)
    assert view.preset_choice.GetStringSelection() == 'Custom'

    selection_calls = []
    original_set_string_selection = view.preset_choice.SetStringSelection

    def _tracked_set_string_selection(value):
        selection_calls.append(str(value))
        return original_set_string_selection(value)

    view.preset_choice.SetStringSelection = _tracked_set_string_selection

    view._band_sliders['bass'].SetValue(190)
    view._band_sliders['bass'].trigger('EVT_SLIDER', None)

    assert ('set_band_gain', 'bass', 7.0) in controller.calls
    assert view.preset_choice.GetStringSelection() == 'Custom'
    assert selection_calls == []


def test_wx_equalizer_view_slider_change_does_not_rebuild_choice_items_when_custom_already_present(monkeypatch):
    view, controller, _event_bus, _localization, _theme = build_equalizer_view(monkeypatch)

    set_items_calls = []
    original_set_items = view.preset_choice.SetItems

    def _tracked_set_items(items):
        set_items_calls.append(list(items))
        original_set_items(items)

    view.preset_choice.SetItems = _tracked_set_items

    view._band_sliders['bass'].SetValue(180)
    view._band_sliders['bass'].trigger('EVT_SLIDER', None)
    assert set_items_calls == [['Flat', 'Rock', 'Custom']]
    assert view.preset_choice.GetStringSelection() == 'Custom'

    view._band_sliders['bass'].SetValue(190)
    view._band_sliders['bass'].trigger('EVT_SLIDER', None)

    assert ('set_band_gain', 'bass', 7.0) in controller.calls
    assert set_items_calls == [['Flat', 'Rock', 'Custom']]
    assert view.preset_choice.GetStringSelection() == 'Custom'


class DelayedPresetRegistryController(DummyEqualizerController):
    """Simula un backend che aggiorna subito lo stato corrente ma non il catalogo preset."""

    def save_current_as_custom_preset(self, preset_name):
        self.calls.append(('save_preset', preset_name))
        self.state['preset_name'] = str(preset_name)
        self.state['band_gains'] = dict(self.state['band_gains'])
        return True



def test_wx_equalizer_view_saved_custom_preset_stays_visible_when_backend_registry_lags(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    localization = DummyLocalizationManager()
    theme = DummyThemeManager()
    event_bus = DummyEventBus()
    controller = DelayedPresetRegistryController()
    view = EqualizerView(
        FakeWxModule.Panel(None),
        equalizer_controller=controller,
        localization_manager=localization,
        theme_manager=theme,
        event_bus=event_bus,
    )

    view._band_sliders['bass'].SetValue(180)
    view._band_sliders['bass'].trigger('EVT_SLIDER', None)
    FakeTextEntryDialog.next_value = 'Mio EQ'
    FakeTextEntryDialog.next_result = FakeWxModule.ID_OK

    view._on_save_preset()

    assert ('save_preset', 'Mio EQ') in controller.calls
    assert view.preset_choice.items == ['Flat', 'Rock', 'Mio EQ']
    assert view.preset_choice.GetStringSelection() == 'Mio EQ'
    assert view.delete_preset_button.IsEnabled() is True


class FailingPresetSaveController(DummyEqualizerController):
    def save_current_as_custom_preset(self, preset_name):
        self.calls.append(('save_preset', preset_name))
        return False



def test_wx_equalizer_view_does_not_add_ghost_preset_when_save_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    localization = DummyLocalizationManager()
    theme = DummyThemeManager()
    event_bus = DummyEventBus()
    controller = FailingPresetSaveController()
    view = EqualizerView(
        FakeWxModule.Panel(None),
        equalizer_controller=controller,
        localization_manager=localization,
        theme_manager=theme,
        event_bus=event_bus,
    )

    FakeTextEntryDialog.next_value = 'Broken EQ'
    FakeTextEntryDialog.next_result = FakeWxModule.ID_OK

    view._on_save_preset()

    assert ('save_preset', 'Broken EQ') in controller.calls
    assert view.preset_choice.items == ['Flat', 'Rock']
    assert view.preset_choice.GetStringSelection() == 'Flat'


def test_wx_equalizer_view_save_preset_uses_safe_dialog_getter(monkeypatch):
    view, controller, _event_bus, _localization, _theme = build_equalizer_view(monkeypatch)

    original_get_value = FakeTextEntryDialog.GetValue
    original_get_text_value = FakeTextEntryDialog.GetTextValue

    def _missing_get_value(self):
        raise AttributeError('GetValue unavailable')

    def _get_text_value(self):
        return 'LegacyName'

    monkeypatch.setattr(FakeTextEntryDialog, 'GetValue', _missing_get_value, raising=True)
    monkeypatch.setattr(FakeTextEntryDialog, 'GetTextValue', _get_text_value, raising=True)
    FakeTextEntryDialog.next_result = FakeWxModule.ID_OK

    view._on_save_preset()

    assert ('save_preset', 'LegacyName') in controller.calls
    assert 'LegacyName' in view.preset_choice.items

    monkeypatch.setattr(FakeTextEntryDialog, 'GetValue', original_get_value, raising=True)
    monkeypatch.setattr(FakeTextEntryDialog, 'GetTextValue', original_get_text_value, raising=True)



def test_wx_equalizer_view_custom_preset_event_payload_refreshes_menu(monkeypatch):
    view, controller, event_bus, _localization, _theme = build_equalizer_view(monkeypatch)

    controller.preset_names = ['Flat', 'Rock']
    controller.state['preset_name'] = 'mio eq'
    event_bus.publish(AudioEventType.CUSTOM_PRESETS_UPDATED, {'type': 'eq', 'presets': ['Flat', 'Rock', 'mio eq']})

    assert 'mio eq' in view.preset_choice.items
    assert view.preset_choice.GetStringSelection() == 'mio eq'
