from __future__ import annotations

import sys

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.main_view import MainView
from src.ui_wx.settings_view import SettingsView
from tests.wx_fakes import FakeApp, FakeWxModule


class DummyLocalizationManager:
    def __init__(self, language: str = 'it') -> None:
        self.language = language
        self.language_callbacks = []

    def register_language_change_callback(self, callback):
        self.language_callbacks.append(callback)

    def unregister_language_change_callback(self, callback):
        if callback in self.language_callbacks:
            self.language_callbacks.remove(callback)

    def get_current_language(self):
        return self.language

    def get_available_languages(self):
        return {'en': 'English', 'it': 'Italiano', 'fr': 'Français'}

    def get_text(self, key, default=None, **kwargs):
        mapping = {
            'nav_settings': 'Impostazioni',
            'settings_title': 'Impostazioni',
            'settings_language': 'Lingua',
            'settings_default_volume': 'Volume predefinito',
            'settings_app_theme': 'Tema app',
            'settings_maintenance_section_title': 'Manutenzione',
            'settings_maintenance_section_note': 'Strumenti utili per dati app, log e cache.',
            'settings_open_app_data': 'Apri cartella dati app',
            'settings_open_processed_audio': 'Apri cartella audio processato',
            'settings_open_logs': 'Apri cartella log',
            'settings_open_third_party_notices': 'ThirdPartyNotices',
            'settings_clear_processed_audio': 'Pulisci cache audio processato',
            'settings_clear_general_cache': 'Pulisci cache e log',
            'settings_clear_cache_confirm_title': 'Conferma pulizia',
            'settings_clear_cache_confirm_body': "Vuoi pulire il contenuto di '{name}'?",
            'settings_clear_cache_done': 'Pulizia completata. Rimossi {count} elemento/i.',
            'settings_open_folder_failed': 'Impossibile aprire la cartella richiesta.',
            'settings_processed_audio_busy': 'Ferma la riproduzione audio corrente prima di pulire la cache audio processato.',
            'theme_system': 'Sistema',
            'theme_dark': 'Scuro',
            'theme_light': 'Chiaro',
            'theme_green': 'Verde',
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

    def get_current_theme_name(self):
        return 'dark'

    def get_available_theme_names(self):
        return ['dark', 'light', 'green']

    def get_current_theme_colors(self):
        return {
            'bg_color': '#111111',
            'panel_bg': '#1b1b1b',
            'text_color': '#f5f5f5',
            'button_color': '#222222',
        }


class DummySettingsManager:
    def __init__(self) -> None:
        self.values = {'volume': 65, 'theme': 'dark'}
        self.set_calls = []

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value
        self.set_calls.append((key, value))


class DummyEventBus:
    def __init__(self) -> None:
        self.published = []

    def publish(self, event_type, payload):
        self.published.append((event_type, payload))


class DummySettingsController:
    def __init__(self, settings_manager: DummySettingsManager) -> None:
        self.settings_manager = settings_manager
        self.calls = []
        self.block_message = ''
        self.processed_audio_cleared = 0
        self.runtime_cleared = 0

    def set_setting(self, key, value):
        self.calls.append(('set_setting', key, value))
        self.settings_manager.set_setting(key, value)

    def get_app_data_dir(self):
        return '/tmp/appdata'

    def get_processed_audio_dir(self):
        return '/tmp/processed_audio'

    def get_logs_dir(self):
        return '/tmp/logs'

    def get_third_party_notices_dir(self):
        return '/tmp/notices'

    def get_processed_audio_cleanup_block_message(self):
        return self.block_message

    def clear_processed_audio_cache(self):
        self.processed_audio_cleared += 1
        return 4

    def clear_runtime_artifacts(self):
        self.runtime_cleared += 1
        return {'total_removed': 9}


class BrokenSettingsController(DummySettingsController):
    def get_app_data_dir(self):
        return ''


def build_settings_view(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    localization = DummyLocalizationManager()
    theme = DummyThemeManager()
    settings = DummySettingsManager()
    event_bus = DummyEventBus()
    controller = DummySettingsController(settings)
    view = SettingsView(
        FakeWxModule.Panel(None),
        settings_controller=controller,
        localization_manager=localization,
        settings_manager=settings,
        theme_manager=theme,
        event_bus=event_bus,
    )
    return view, controller, event_bus, settings, localization, theme


def test_wx_settings_view_loads_localized_controls(monkeypatch):
    view, _controller, _event_bus, settings, _localization, _theme = build_settings_view(monkeypatch)

    assert view.title_label.label == 'Impostazioni'
    assert view.language_choice.items == ['English', 'Italiano', 'Français']
    assert view.language_choice.GetSelection() == 1
    assert view.theme_choice.items[1] == 'Scuro'
    assert view.volume_slider.GetValue() == 65
    assert view.volume_value_label.label == '65%'
    assert settings.values['theme'] == 'dark'



def test_wx_settings_view_dispatches_language_theme_and_volume(monkeypatch):
    view, controller, _event_bus, settings, _localization, _theme = build_settings_view(monkeypatch)

    view.language_choice.SetSelection(0)
    view._on_language_changed()
    view.theme_choice.SetSelection(2)
    view._on_theme_changed()
    view.volume_slider.SetValue(80)
    view._on_volume_changed()

    assert ('set_setting', 'language', 'en') in controller.calls
    assert ('set_setting', 'theme', 'light') in controller.calls
    assert ('set_setting', 'volume', 80) in controller.calls
    assert settings.values['volume'] == 80
    assert view.volume_value_label.label == '80%'



def test_wx_settings_view_runs_maintenance_actions(monkeypatch):
    view, controller, event_bus, _settings, _localization, _theme = build_settings_view(monkeypatch)
    opened = []
    monkeypatch.setattr('src.ui_wx.settings_view.open_directory', lambda path: opened.append(str(path)))

    view._on_open_app_data()
    view._on_open_processed_audio()
    view._on_open_logs()
    view._on_open_third_party_notices()
    view._on_clear_processed_audio()
    view._on_clear_runtime()

    assert opened == ['/tmp/appdata', '/tmp/processed_audio', '/tmp/logs', '/tmp/notices']
    assert controller.processed_audio_cleared == 1
    assert controller.runtime_cleared == 1
    assert event_bus.published[-2] == (
        AudioEventType.FEEDBACK_MESSAGE,
        {'message': 'Pulizia completata. Rimossi 4 elemento/i.', 'color': 'green'},
    )
    assert event_bus.published[-1] == (
        AudioEventType.FEEDBACK_MESSAGE,
        {'message': 'Pulizia completata. Rimossi 9 elemento/i.', 'color': 'green'},
    )



def test_wx_settings_view_reports_failures_and_cleanup_block(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    localization = DummyLocalizationManager()
    theme = DummyThemeManager()
    settings = DummySettingsManager()
    event_bus = DummyEventBus()
    controller = BrokenSettingsController(settings)
    controller.block_message = 'Ferma la riproduzione audio corrente prima di pulire la cache audio processato.'
    view = SettingsView(
        FakeWxModule.Panel(None),
        settings_controller=controller,
        localization_manager=localization,
        settings_manager=settings,
        theme_manager=theme,
        event_bus=event_bus,
    )

    view._on_open_app_data()
    view._on_clear_processed_audio()
    view._on_clear_runtime()

    assert event_bus.published[0] == (
        AudioEventType.FEEDBACK_MESSAGE,
        {'message': 'Impossibile aprire la cartella richiesta.', 'color': 'orange'},
    )
    assert event_bus.published[-2] == (
        AudioEventType.FEEDBACK_MESSAGE,
        {'message': 'Ferma la riproduzione audio corrente prima di pulire la cache audio processato.', 'color': 'orange'},
    )
    assert event_bus.published[-1] == (
        AudioEventType.FEEDBACK_MESSAGE,
        {'message': 'Ferma la riproduzione audio corrente prima di pulire la cache audio processato.', 'color': 'orange'},
    )



def test_wx_main_view_instantiates_settings_page(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    localization = DummyLocalizationManager()
    settings = DummySettingsManager()
    theme = DummyThemeManager()
    main_view = MainView(
        FakeApp(),
        localization_manager=localization,
        theme_manager=theme,
        settings_manager=settings,
        settings_controller=DummySettingsController(settings),
        event_bus=DummyEventBus(),
    )

    assert main_view.get_view('settings').owner.__class__.__name__ == 'SettingsView'
    assert main_view._sidebar_buttons['settings'].label == 'Impostazioni'
