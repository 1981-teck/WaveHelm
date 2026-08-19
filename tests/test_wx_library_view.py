from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from src.audio.audio_event_models import AudioEventType
from src.model.media_file import MediaFile, MediaType
from src.ui_wx.library_view import LibraryView
from src.ui_wx.main_view import MainView
from tests.wx_fakes import FakeApp, FakeDirDialog, FakeFileDialog, FakeWxModule


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
            'nav_library': 'Libreria',
            'library_search_label': 'Cerca',
            'library_filter_label': 'Filtro',
            'library_sort_label': 'Ordina',
            'library_column_title': 'Titolo',
            'library_column_artist': 'Artista',
            'library_column_album': 'Album',
            'library_column_duration': 'Durata',
            'library_column_type': 'Tipo',
            'library_column_path': 'Percorso',
            'library_filter_all': 'Tutti',
            'library_filter_audio': 'Audio',
            'library_filter_video': 'Video',
            'library_sort_title': 'Titolo',
            'library_sort_artist': 'Artista',
            'library_sort_album': 'Album',
            'library_sort_duration': 'Durata',
            'add_files_button': 'Aggiungi File',
            'add_folder_button': 'Aggiungi Cartella',
            'btn_refresh': 'Aggiorna',
            'tooltip_play': 'Riproduci Selezionati',
            'nav_favorites': 'Preferiti',
            'tooltip_remove': 'Rimuovi Selezionati',
            'library_type_audio': 'Audio',
            'library_type_video': 'Video',
            'library_status': 'Elementi visibili: {count} / {total}',
            'library_select_media_first': 'Seleziona almeno un elemento della libreria.',
            'library_select_favorites_first': 'Seleziona almeno un elemento da aggiungere ai preferiti.',
            'library_favorites_added': 'Aggiunti {count} elemento/i ai preferiti.',
            'library_removed_feedback': 'Rimossi {count} elemento/i dalla libreria.',
            'library_import_done': 'Import completato.',
            'library_now_playing_feedback': 'In riproduzione: {title}',
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
            'bg_color': '#111111',
            'panel_bg': '#1b1b1b',
            'text_color': '#f5f5f5',
            'button_color': '#222222',
        }


class DummySettingsManager:
    def __init__(self) -> None:
        self.values = {}

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value


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


@dataclass
class DummyPlaybackContext:
    items: list[MediaFile]
    start_index: int
    autoplay: bool


class DummyPlayerController:
    def __init__(self) -> None:
        self.playback_contexts: list[DummyPlaybackContext] = []

    def set_playback_context(self, items, start_index, autoplay):
        self.playback_contexts.append(DummyPlaybackContext(list(items), int(start_index), bool(autoplay)))


class DummyLibraryController:
    def __init__(self, event_bus: DummyEventBus) -> None:
        self.event_bus = event_bus
        self.media = [
            MediaFile('/tmp/alpha.mp3', title='Alpha', media_type=MediaType.AUDIO, duration=90.0, metadata={'artist': 'Able', 'album': 'One'}),
            MediaFile('/tmp/bravo.mp4', title='Bravo', media_type=MediaType.VIDEO, duration=180.0, metadata={'artist': 'Baker', 'album': 'Two'}),
        ]
        self.favorite_paths = set()
        self.add_calls = []
        self.remove_calls = []

    def get_all_media(self):
        return list(self.media)

    def add_media_files(self, paths, emit_event=True, emit_feedback=True):
        self.add_calls.append((list(paths), bool(emit_event), bool(emit_feedback)))
        for path in paths:
            if any(item.path == path for item in self.media):
                continue
            media_type = MediaType.VIDEO if str(path).endswith('.mp4') else MediaType.AUDIO
            title = str(path).split('/')[-1].split('.')[0].title()
            self.media.append(MediaFile(str(path), title=title, media_type=media_type, duration=60.0, metadata={'artist': 'Imported'}))
        if emit_event:
            self.event_bus.publish(AudioEventType.LIBRARY_UPDATED, {'count': len(paths)})

    def remove_media_by_path(self, path, emit_event=True, emit_feedback=True):
        before = len(self.media)
        self.media = [item for item in self.media if item.path != path]
        removed = len(self.media) != before
        if removed:
            self.remove_calls.append(path)
            if emit_event:
                self.event_bus.publish(AudioEventType.LIBRARY_UPDATED, {'count': len(self.media)})
        return removed

    def add_to_favorites(self, media, emit_feedback=False, emit_event=True):
        if media.path in self.favorite_paths:
            return False
        self.favorite_paths.add(media.path)
        if emit_event:
            self.event_bus.publish(AudioEventType.FAVORITE_CHANGED, {'path': media.path, 'is_favorite': True})
        return True



def build_library_view(monkeypatch, settings_manager=None):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    event_bus = DummyEventBus()
    if settings_manager is None:
        settings_manager = DummySettingsManager()
    view = LibraryView(
        FakeWxModule.Panel(None),
        library_controller=DummyLibraryController(event_bus),
        player_controller=DummyPlayerController(),
        localization_manager=DummyLocalizationManager(),
        theme_manager=DummyThemeManager(),
        event_bus=event_bus,
        settings_manager=settings_manager,
    )
    return view, view.library_controller, view.player_controller, event_bus, settings_manager



def test_wx_library_view_populates_rows_and_localized_labels(monkeypatch):
    view, _library, _player, _event_bus, _settings = build_library_view(monkeypatch)

    assert view.title_label.label == 'Libreria'
    assert view.search_label.label == 'Cerca'
    assert view.filter_label.label == 'Filtro'
    assert view.sort_label.label == 'Ordina'
    assert view.table.columns == ['Titolo', 'Artista', 'Album', 'Durata', 'Tipo', 'Percorso']
    assert view.add_files_button.label == 'Aggiungi File'
    assert view.add_folder_button.label == 'Aggiungi Cartella'
    assert view.refresh_button.label == 'Aggiorna'
    assert view.play_button.label == 'Riproduci Selezionati'
    assert view.favorite_button.label == 'Preferiti'
    assert view.remove_button.label == 'Rimuovi Selezionati'
    assert view.table.GetItemCount() == 2
    assert view.table.GetItemText(0, 0) == 'Alpha'
    assert view.status_label.label == 'Elementi visibili: 2 / 2'



def test_wx_library_view_filters_and_sorts_rendered_media(monkeypatch):
    view, _library, _player, _event_bus, _settings = build_library_view(monkeypatch)

    view.search_text.SetValue('bravo')
    view._on_search_changed()
    assert view.table.GetItemCount() == 1
    assert view.table.GetItemText(0, 0) == 'Bravo'

    view.search_text.SetValue('')
    view.filter_choice.SetSelection(1)
    view._on_filter_changed()
    assert view.table.GetItemCount() == 1
    assert view.table.GetItemText(0, 4) == 'Audio'

    view.filter_choice.SetSelection(0)
    view.sort_choice.SetSelection(3)
    view._on_sort_changed()
    assert view.table.GetItemText(0, 0) == 'Alpha'



def test_wx_library_view_dispatches_play_favorites_and_remove(monkeypatch):
    view, library, player, event_bus, _settings = build_library_view(monkeypatch)

    view.table.Select(1)
    view._on_play_selected()
    assert player.playback_contexts[0].start_index == 1
    assert player.playback_contexts[0].autoplay is True

    view._on_add_to_favorites()
    bravo_path = os.path.normpath('/tmp/bravo.mp4')
    assert bravo_path in library.favorite_paths

    view._on_remove_selected()
    assert library.remove_calls == [bravo_path]
    assert view.table.GetItemCount() == 1
    assert event_bus.published[-1] == (
        AudioEventType.FEEDBACK_MESSAGE,
        {'message': 'Rimossi 1 elemento/i dalla libreria.', 'color': 'green'},
    )



def test_wx_library_view_imports_from_dialogs_and_updates_rows(monkeypatch):
    view, library, _player, event_bus, _settings = build_library_view(monkeypatch)
    FakeFileDialog.next_paths = ['/tmp/charlie.mp3']
    FakeFileDialog.next_result = FakeWxModule.ID_OK
    FakeDirDialog.next_paths = ['/tmp/media_folder']
    FakeDirDialog.next_result = FakeWxModule.ID_OK

    view._on_add_files()
    view._on_add_folder()

    assert library.add_calls == [
        (['/tmp/charlie.mp3'], True, True),
        (['/tmp/media_folder'], True, True),
    ]
    assert view.table.GetItemCount() == 4
    assert event_bus.published[-1] == (
        AudioEventType.FEEDBACK_MESSAGE,
        {'message': 'Import completato.', 'color': 'green'},
    )



def test_wx_library_view_reacts_to_external_events(monkeypatch):
    view, library, _player, event_bus, _settings = build_library_view(monkeypatch)

    delta = MediaFile('/tmp/delta.mp3', title='Delta', media_type=MediaType.AUDIO, duration=45.0)
    library.media.append(delta)
    event_bus.publish(AudioEventType.LIBRARY_UPDATED, {'count': 3})
    assert view.table.GetItemCount() == 3

    event_bus.publish(AudioEventType.MEDIA_DURATION_UPDATE, {'path': delta.path, 'duration': 125.0})
    assert view.table.GetItemText(2, 3) == '02:05'

    event_bus.publish(AudioEventType.PLAYER_STATE_CHANGED, {'current_track': {'title': 'Delta'}})
    assert view.feedback_label.label == 'In riproduzione: Delta'



def test_wx_main_view_instantiates_real_library_page(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    event_bus = DummyEventBus()
    main_view = MainView(
        FakeApp(),
        localization_manager=DummyLocalizationManager(),
        theme_manager=DummyThemeManager(),
        event_bus=event_bus,
        library_controller=DummyLibraryController(event_bus),
        player_controller=DummyPlayerController(),
    )

    assert main_view.get_view('library').owner.__class__.__name__ == 'LibraryView'
    assert main_view._sidebar_buttons['library'].label == 'Libreria'


def test_wx_library_view_persists_column_widths_across_sessions(monkeypatch):
    settings = DummySettingsManager()
    view, _library, _player, _event_bus, settings = build_library_view(monkeypatch, settings_manager=settings)

    view.table.SetColumnWidth(0, 240)
    view.table.SetColumnWidth(5, 480)
    view._on_table_column_resized(None)

    restored, _library2, _player2, _event_bus2, _settings2 = build_library_view(monkeypatch, settings_manager=settings)
    assert restored.table.GetColumnWidth(0) == 240
    assert restored.table.GetColumnWidth(5) == 480


def test_wx_library_view_persists_column_widths_on_shutdown_without_drag_event(monkeypatch):
    settings = DummySettingsManager()
    view, _library, _player, _event_bus, settings = build_library_view(monkeypatch, settings_manager=settings)

    view.table.SetColumnWidth(1, 333)
    view.table.SetColumnWidth(4, 222)
    view.shutdown()

    restored, _library2, _player2, _event_bus2, _settings2 = build_library_view(monkeypatch, settings_manager=settings)
    assert restored.table.GetColumnWidth(1) == 333
    assert restored.table.GetColumnWidth(4) == 222
