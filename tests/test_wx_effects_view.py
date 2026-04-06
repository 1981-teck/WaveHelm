from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.effects_view import EffectsView
from tests.wx_fakes import FakeFileDialog, FakeWxModule


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
            'nav_effects': 'Effetti',
            'effects_title': 'Effetti',
            'save_effects_settings_button': 'Salva impostazioni',
            'effects_all_reset': 'Reset totale',
            'effect_echo': 'Eco',
            'effect_reverb': 'Riverbero',
            'effect_vintage_filter': 'Filtro vintage',
            'effects_toggle': 'Attivo',
            'effect_echo_delay': 'Ritardo',
            'effect_echo_decay': 'Decay',
            'effect_reverb_decay': 'Decay riverbero',
            'effect_reverb_wet': 'Wet',
            'effect_vintage_cutoff': 'Cutoff',
            'effect_vintage_resonance': 'Risonanza',
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
            'bg_color': '#0f0f0f',
            'panel_bg': '#171717',
            'text_color': '#f4f4f5',
            'button_color': '#232323',
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


class DummyEffectsController:
    def __init__(self) -> None:
        self.settings = {
            'echo': {'enabled': True, 'delay_ms': 300.0, 'decay': 0.5},
            'reverb': {'enabled': False, 'decay_time': 1.5, 'wet_level': 0.3},
            'vintage_filter': {'enabled': True, 'cutoff_freq': 1200.0, 'resonance': 0.4},
        }
        self.calls = []
        self.saved = 0
        self.resets = 0

    def get_all_effects_settings(self):
        return self.settings

    def set_effect_enabled(self, effect_name, enabled):
        self.calls.append(('set_effect_enabled', effect_name, bool(enabled)))
        self.settings[effect_name]['enabled'] = bool(enabled)

    def set_effect_parameter(self, effect_name, param_name, value):
        self.calls.append(('set_effect_parameter', effect_name, param_name, round(float(value), 2)))
        self.settings[effect_name][param_name] = float(value)

    def save_current_settings_to_profile(self):
        self.saved += 1
        return True

    def reset_all_effects(self):
        self.resets += 1
        self.settings = {
            'echo': {'enabled': False, 'delay_ms': 0.0, 'decay': 0.0},
            'reverb': {'enabled': False, 'decay_time': 0.1, 'wet_level': 0.0},
            'vintage_filter': {'enabled': False, 'cutoff_freq': 20.0, 'resonance': 0.0},
        }


class DummyEffectsEngine:
    def __init__(self) -> None:
        self.sample_rate = 44100
        self.channels = 2

    def apply_effects(self, audio_data):
        return np.clip(np.asarray(audio_data, dtype=np.float32) * 0.5, -1.0, 1.0)


class DummyTrack:
    title = 'Demo Track'
    path = 'demo_track.wav'
    media_type = 'AUDIO'


class DummyPlayerController:
    def __init__(self) -> None:
        self.current_track = DummyTrack()


class DummyAudioEngine:
    def __init__(self) -> None:
        self.current_file = None
        self.playback_source_file = None



def build_effects_view(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    localization = DummyLocalizationManager()
    theme = DummyThemeManager()
    event_bus = DummyEventBus()
    controller = DummyEffectsController()
    effects_engine = DummyEffectsEngine()
    audio_engine = DummyAudioEngine()
    player_controller = DummyPlayerController()
    view = EffectsView(
        FakeWxModule.Panel(None),
        effects_engine=effects_engine,
        effects_controller=controller,
        audio_engine=audio_engine,
        player_controller=player_controller,
        localization_manager=localization,
        theme_manager=theme,
        event_bus=event_bus,
    )
    return view, controller, effects_engine, audio_engine, player_controller, event_bus, localization, theme



def test_wx_effects_view_loads_localized_sections_and_values(monkeypatch):
    view, _controller, _effects_engine, _audio_engine, _player_controller, _event_bus, _localization, _theme = build_effects_view(monkeypatch)

    assert view.title_label.label == 'Effetti'
    assert view.save_button.label == 'Salva impostazioni'
    assert view.save_audio_button.label == 'Save audio'
    assert view._section_widgets['echo']['title'].label == 'Eco'
    assert view._section_widgets['echo']['enabled'].GetValue() is True
    assert view._section_widgets['echo']['delay_ms_value'].label == '300'
    assert view._section_widgets['reverb']['wet_level_value'].label == '0.30'



def test_wx_effects_view_dispatches_toggle_slider_save_and_reset(monkeypatch):
    view, controller, _effects_engine, _audio_engine, _player_controller, _event_bus, _localization, _theme = build_effects_view(monkeypatch)

    view._section_widgets['echo']['enabled'].SetValue(False)
    view._section_widgets['echo']['enabled'].trigger('EVT_CHECKBOX', None)
    view._section_widgets['reverb']['wet_level_slider'].SetValue(75)
    view._section_widgets['reverb']['wet_level_slider'].trigger('EVT_SLIDER', None)
    view.save_button.click()
    view.reset_all_button.click()

    assert ('set_effect_enabled', 'echo', False) in controller.calls
    assert ('set_effect_parameter', 'reverb', 'wet_level', 0.75) in controller.calls
    assert controller.saved == 1
    assert controller.resets == 1
    assert view._section_widgets['echo']['enabled'].GetValue() is False
    assert view._section_widgets['vintage_filter']['cutoff_freq_value'].label == '20'



def test_wx_effects_view_refreshes_from_event_bus_and_shutdown(monkeypatch):
    view, _controller, _effects_engine, _audio_engine, _player_controller, event_bus, localization, theme = build_effects_view(monkeypatch)

    event_bus.publish(
        AudioEventType.EFFECTS_CHANGED,
        {
            'settings': {
                'echo': {'enabled': True, 'delay_ms': 450.0, 'decay': 0.7},
                'reverb': {'enabled': True, 'decay_time': 2.0, 'wet_level': 0.6},
                'vintage_filter': {'enabled': False, 'cutoff_freq': 800.0, 'resonance': 0.2},
            }
        },
    )
    event_bus.publish(AudioEventType.FEEDBACK_MESSAGE, {'message': 'Effects updated', 'color': 'green'})

    assert view.feedback_label.label == 'Effects updated'
    assert view._section_widgets['echo']['delay_ms_value'].label == '450'
    assert view._section_widgets['reverb']['enabled'].GetValue() is True

    view.shutdown()

    assert localization.language_callbacks == []
    assert theme.theme_callbacks == []
    assert event_bus.subscriptions.get(AudioEventType.EFFECTS_CHANGED) == []


def test_wx_effects_view_exports_audio_and_opens_saved_folder(monkeypatch, tmp_path):
    view, _controller, _effects_engine, audio_engine, _player_controller, _event_bus, _localization, _theme = build_effects_view(monkeypatch)
    source_path = tmp_path / 'source.wav'
    export_dir = tmp_path / 'effects saved'
    output_path = export_dir / 'custom_export.wav'
    sf.write(str(source_path), np.full((32, 2), 0.2, dtype=np.float32), 44100, subtype='PCM_16')
    audio_engine.current_file = str(source_path)

    monkeypatch.setattr('src.ui_wx.effects_view.get_app_data_path', lambda *parts, create=True: export_dir)
    opened_paths = []
    monkeypatch.setattr('src.ui_wx.effects_view.open_directory', lambda path: opened_paths.append(Path(path)))
    FakeFileDialog.next_paths = [str(output_path)]
    FakeFileDialog.next_result = FakeWxModule.ID_OK

    view.save_audio_button.click()
    view.open_saved_folder_button.click()

    assert output_path.exists() is True
    assert 'custom_export.wav' in view.feedback_label.label or str(export_dir) in view.feedback_label.label
    assert opened_paths == [export_dir]
