from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.about_view import AboutView
from src.ui_wx.ambient_view import AmbientView
from src.ui_wx.main_view import MainView
from src.ui_wx.readmi_view import ReadmiView
from tests.wx_fakes import FakeApp, FakeWxModule


class DummyLocalizationManager:
    def __init__(self, language: str = 'it') -> None:
        self.language = language
        self.language_callbacks = []

    def register_language_change_callback(self, callback):
        self.language_callbacks.append(callback)

    def get_current_language(self):
        return self.language

    def get_text(self, key, default=None, **kwargs):
        mapping = {
            'nav_about': 'Info',
            'nav_readmi': 'Readmi',
            'nav_ambient': 'Ambiente',
            'nav_library': 'Libreria',
            'nav_playlists': 'Playlist',
            'nav_favorites': 'Preferiti',
            'version_label': 'Versione: {version}',
            'author_label': 'Autore: {author}',
            'license_label': 'Licenza: {license}',
            'target_platform_label': 'Piattaforma: {target_platform}',
            'website_label': 'Sito: {website_url}',
            'modules_enabled_title': 'Moduli attivi',
            'libraries_used_title': 'Librerie usate',
            'contact_title': 'Contatti',
            'contact_email': 'Email',
            'export_app_info_button': 'Esporta info app',
            'ambient_sounds_title': 'Suoni ambientali',
            'ambient_sounds_subtitle': 'Riproduci sottofondi separati.',
            'ambient_sound_label': 'Suono ambientale',
            'ambient_refresh_button': 'Aggiorna',
            'ambient_open_folder_button': 'Apri cartella',
            'play_ambient_sound_button': 'Avvia',
            'stop_ambient_sound_button': 'Ferma',
            'ambient_volume_label': 'Volume',
            'mute_ambient_label': 'Muto',
            'ambient_status_idle': 'Nessun suono ambientale attivo.',
            'ambient_status_playing': 'In riproduzione: {name}',
            'ambient_status_selected': 'Selezionato: {name}',
            'ambient_folder_hint': 'Cartella ambient: {folder}',
            'ambient_sound_started': 'Suono ambient avviato: {file}',
            'ambient_sound_stopped': 'Suono ambient fermato.',
            'ambient_select_sound_first': 'Seleziona prima un suono ambientale.',
            'ambient_save_mix_button': 'Salva mix',
            'ambient_open_mix_folder_button': 'Apri cartella mix',
            'ambient_open_mix_folder_success': 'Cartella mix ambient aperta: {folder}.',
            'ambient_save_mix_success': 'Mix audio esportato in {path}.',
            'ambient_save_mix_requires_audio': 'Avvia una traccia audio prima di esportare il mix.',
        }
        text = mapping.get(key, default or key)
        return text.format(**kwargs) if kwargs else text


class DummyThemeManager:
    def __init__(self) -> None:
        self.theme_callbacks = []

    def register_theme_change_callback(self, callback):
        self.theme_callbacks.append(callback)

    def get_current_theme_colors(self):
        return {
            'bg_color': '#111111',
            'panel_bg': '#1b1b1b',
            'text_color': '#f5f5f5',
            'button_color': '#222222',
            'selection_bg': '#00aaff',
        }


class DummyAmbientManager:
    def __init__(self) -> None:
        self.available = ['Rain', 'Forest']
        self.current_name = None
        self.volume = 0.4
        self.muted = False
        self.play_calls = []
        self.stop_calls = 0
        self.export_calls = []

    def get_available_ambient_sounds(self):
        return list(self.available)

    def get_primary_ambient_dir(self):
        return Path('/tmp/ambient')

    def get_current_sound_name(self):
        return self.current_name

    def get_ambient_volume(self):
        return self.volume

    def is_ambient_muted(self):
        return self.muted

    def is_playing(self):
        return self.current_name is not None

    def play_ambient_sound(self, name):
        self.play_calls.append(name)
        self.current_name = name
        return True

    def stop_ambient_sound(self):
        self.stop_calls += 1
        self.current_name = None

    def set_ambient_volume(self, value):
        self.volume = float(value)

    def set_ambient_muted(self, muted):
        self.muted = bool(muted)

    def get_current_source(self):
        return self.current_name

    def get_mix_export_dir(self):
        return Path('/tmp/ambient mix saved')

    def export_audio_mix(self, **kwargs):
        self.export_calls.append(dict(kwargs))
        output_path = Path(kwargs['output_path'])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b'mix')
        return output_path


class DummyAudioEngine:
    def __init__(self, source: str = '/tmp/main_track.wav', volume: float = 0.7, muted: bool = False) -> None:
        self.playback_source_file = source
        self.current_file = source
        self._volume = float(volume)
        self._muted = bool(muted)

    def get_volume(self):
        return self._volume

    def is_muted(self):
        return self._muted


class DummyPlayerController:
    def __init__(self, path: str = '/tmp/main_track.wav') -> None:
        self.current_track = SimpleNamespace(path=path, title='Main Track', media_type=SimpleNamespace(name='AUDIO'))


class DummyEventBus:
    def __init__(self) -> None:
        self.subscriptions = {}

    def subscribe(self, event_type, callback):
        self.subscriptions.setdefault(event_type, []).append(callback)
        return callback

    def unsubscribe(self, event_type, subscription=None):
        if subscription in self.subscriptions.get(event_type, []):
            self.subscriptions[event_type].remove(subscription)
            return True
        return False

    def publish(self, event_type, payload):
        for callback in list(self.subscriptions.get(event_type, [])):
            callback(payload)


def test_wx_about_view_updates_labels_and_exports(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    monkeypatch.setattr('src.ui_wx.about_view.get_app_data_path', lambda *parts, **kwargs: tmp_path / 'wavehelm_app_info.json')
    view = AboutView(FakeWxModule.Panel(None), localization_manager=DummyLocalizationManager(), theme_manager=DummyThemeManager())

    assert 'Versione:' in view._info_labels['version'].label
    assert 'GPL' in view._info_labels['license'].label
    view._on_export()
    exported = (tmp_path / 'wavehelm_app_info.json').read_text(encoding='utf-8')
    assert 'WaveHelm' in exported


def test_wx_readmi_view_loads_localized_manual(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    localization = DummyLocalizationManager('it')
    view = ReadmiView(FakeWxModule.Panel(None), localization_manager=localization, theme_manager=DummyThemeManager())

    initial_content = getattr(view.browser, 'html', getattr(view.browser, 'value', ''))
    assert 'Manuale utente di WaveHelm' in initial_content
    localization.language = 'en'
    view.update_localization()
    updated_content = getattr(view.browser, 'html', getattr(view.browser, 'value', ''))
    assert 'WaveHelm User Manual' in updated_content


def test_wx_ambient_view_controls_manager_and_receives_events(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    manager = DummyAmbientManager()
    event_bus = DummyEventBus()
    view = AmbientView(
        FakeWxModule.Panel(None),
        ambient_manager=manager,
        event_bus=event_bus,
        localization_manager=DummyLocalizationManager(),
        theme_manager=DummyThemeManager(),
        player_controller=DummyPlayerController(),
        audio_engine=DummyAudioEngine(),
    )

    assert view.sound_choice.items == ['Rain', 'Forest']
    view.sound_choice.SetStringSelection('Forest')
    view._on_sound_selected()
    view._on_play()
    assert manager.play_calls == ['Forest']
    event_bus.publish(AudioEventType.AMBIENT_VOLUME, {'volume': 0.75})
    assert view.volume_slider.GetValue() == 75
    view._on_stop()
    assert manager.stop_calls == 1


def test_wx_ambient_view_opens_mix_export_folder(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    manager = DummyAmbientManager()
    manager.get_mix_export_dir = lambda: tmp_path / 'ambient mix saved'
    opened = []
    monkeypatch.setattr('src.ui_wx.ambient_view.open_directory', lambda path: opened.append(Path(path)))

    view = AmbientView(
        FakeWxModule.Panel(None),
        ambient_manager=manager,
        event_bus=DummyEventBus(),
        localization_manager=DummyLocalizationManager(),
        theme_manager=DummyThemeManager(),
        player_controller=DummyPlayerController(),
        audio_engine=DummyAudioEngine(),
    )

    assert view.open_mix_folder_button.label == 'Apri cartella mix'
    view._on_open_mix_folder()

    expected = tmp_path / 'ambient mix saved'
    assert opened == [expected]
    assert expected.exists()
    assert 'Cartella mix ambient aperta:' in view.feedback_label.label


def test_wx_main_view_instantiates_real_simple_pages(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    main_view = MainView(
        FakeApp(),
        localization_manager=DummyLocalizationManager(),
        theme_manager=DummyThemeManager(),
        ambient_manager=DummyAmbientManager(),
        event_bus=DummyEventBus(),
    )

    assert main_view.get_view('about').owner.__class__.__name__ == 'AboutView'
    assert main_view.get_view('readmi').owner.__class__.__name__ == 'ReadmiView'
    assert main_view.get_view('ambient').owner.__class__.__name__ == 'AmbientView'
    assert main_view._sidebar_buttons['about'].label == 'Info'

def test_wx_main_view_sidebar_labels_exclude_video_and_keep_supported_pages(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    main_view = MainView(
        FakeApp(),
        localization_manager=DummyLocalizationManager(),
        theme_manager=DummyThemeManager(),
        ambient_manager=DummyAmbientManager(),
        event_bus=DummyEventBus(),
    )

    visible_pages = list(main_view._sidebar_buttons)

    assert 'video' not in visible_pages
    assert visible_pages == [
        'library',
        'playlist',
        'favorites',
        'equalizer',
        'effects',
        'visualizer',
        'ambient',
        'settings',
        'readmi',
        'about',
    ]



def test_wx_ambient_view_can_export_mixed_audio(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    manager = DummyAmbientManager()
    manager.current_name = 'Rain'
    view = AmbientView(
        FakeWxModule.Panel(None),
        ambient_manager=manager,
        event_bus=DummyEventBus(),
        localization_manager=DummyLocalizationManager(),
        theme_manager=DummyThemeManager(),
        player_controller=DummyPlayerController(path=str(tmp_path / 'main_track.wav')),
        audio_engine=DummyAudioEngine(source=str(tmp_path / 'processed_main.wav')),
    )

    FakeWxModule.FileDialog.next_result = FakeWxModule.ID_OK
    FakeWxModule.FileDialog.next_paths = [str(tmp_path / 'ambient mix saved' / 'session_export.wav')]
    view.sound_choice.SetStringSelection('Rain')

    view._on_save_mix()

    assert manager.export_calls
    export_call = manager.export_calls[-1]
    assert export_call['ambient_sound_name_or_path'] == 'Rain'
    assert export_call['main_source_path'] == str(tmp_path / 'processed_main.wav')
    assert Path(export_call['output_path']).name == 'session_export.wav'
    assert 'session_export.wav' in view.feedback_label.label
