from __future__ import annotations

import sys
from dataclasses import dataclass

from src.audio.audio_event_models import AudioEventType
from src.model.media_file import MediaFile, MediaType
from src.ui_wx.favorites_view import FavoritesView
from src.ui_wx.main_view import MainView
from tests.wx_fakes import FakeApp, FakeWxModule


class DummyLocalizationManager:
    def __init__(self) -> None:
        self.language_callbacks = []
        self.mapping = {
            'nav_favorites': 'Preferiti',
            'favorites_header': 'Preferiti',
            'favorites_col_title': 'Titolo',
            'favorites_col_artist': 'Artista',
            'favorites_col_duration': 'Durata',
            'favorites_col_path': 'Percorso',
            'favorites_delete_confirm_title': 'Conferma',
            'favorites_delete_confirm_message': 'Rimuovere {count} elementi dai preferiti?',
            'favorites_status': 'Preferiti: {count}',
            'favorites_remove_button': 'Rimuovi Selezionati',
            'favorites_select_all_button': 'Seleziona Tutto',
            'favorites_select_all_done': 'Tutti i preferiti selezionati.',
            'favorites_select_warning': 'Seleziona un preferito da riprodurre.',
            'favorites_remove_warning': 'Seleziona uno o più preferiti da rimuovere.',
            'favorites_playback_started': '▶ Riproduzione dai preferiti avviata.',
            'items_removed_from_favorites': 'Rimosso(i) {count} elemento(i) dai preferiti.',
            'library_play_selected_button': 'Riproduci Selezionati',
            'library_refresh_button': 'Aggiorna',
        }

    def register_language_change_callback(self, callback):
        self.language_callbacks.append(callback)

    def unregister_language_change_callback(self, callback):
        if callback in self.language_callbacks:
            self.language_callbacks.remove(callback)

    def set_mapping(self, mapping):
        self.mapping = dict(mapping)

    def get_text(self, key, default=None, **kwargs):
        text = self.mapping.get(key, default or key)
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
        self.playback_contexts = []

    def set_playback_context(self, items, start_index, autoplay):
        self.playback_contexts.append(DummyPlaybackContext(list(items), int(start_index), bool(autoplay)))


class DummyDatabaseManager:
    def __init__(self) -> None:
        self.items = [
            {
                'path': '/tmp/favorite_a.mp3',
                'title': 'Alpha',
                'media_type': MediaType.AUDIO.value,
                'duration': 61.0,
                'metadata': {'artist': 'Able'},
            },
            {
                'path': '/tmp/favorite_b.mp4',
                'title': 'Beta',
                'media_type': MediaType.VIDEO.value,
                'duration': 122.0,
                'metadata': {'artist': 'Baker'},
            },
        ]
        self.removed = []

    def get_all_favorite_items(self):
        return list(self.items)

    def remove_favorite(self, path):
        self.removed.append(path)
        self.items = [item for item in self.items if item.get('path') != path]



def build_favorites_view(monkeypatch, settings_manager=None):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    FakeWxModule.next_message_box_result = FakeWxModule.YES
    if settings_manager is None:
        settings_manager = DummySettingsManager()
    event_bus = DummyEventBus()
    database_manager = DummyDatabaseManager()
    player_controller = DummyPlayerController()
    view = FavoritesView(
        FakeWxModule.Panel(None),
        database_manager=database_manager,
        player_controller=player_controller,
        localization_manager=DummyLocalizationManager(),
        event_bus=event_bus,
        theme_manager=DummyThemeManager(),
        settings_manager=settings_manager,
    )
    return view, database_manager, player_controller, event_bus, settings_manager



def test_wx_favorites_view_populates_rows_and_labels(monkeypatch):
    view, _database_manager, _player_controller, _event_bus, _settings = build_favorites_view(monkeypatch)

    assert view.title_label.label == 'Preferiti'
    assert view.play_button.label == 'Riproduci Selezionati'
    assert view.remove_button.label == 'Rimuovi Selezionati'
    assert view.refresh_button.label == 'Aggiorna'
    assert view.select_all_button.label == 'Seleziona Tutto'
    assert view.table.columns == ['Titolo', 'Artista', 'Durata', 'Percorso']
    assert view.table.GetItemCount() == 2
    assert view.table.GetItemText(0, 0) == 'Alpha'
    assert view.table.GetItemText(1, 1) == 'Baker'
    assert view.status_label.label == 'Preferiti: 2'



def test_wx_favorites_view_play_remove_select_all_and_refresh(monkeypatch):
    view, database_manager, player_controller, event_bus, _settings = build_favorites_view(monkeypatch)

    view.table.Select(1, True)
    view._on_play_selected()
    assert player_controller.playback_contexts[-1].start_index == 1
    assert player_controller.playback_contexts[-1].autoplay is True

    view._on_select_all()
    assert view.table.GetFirstSelected() == 0

    view._on_remove_selected()
    assert database_manager.removed == ['/tmp/favorite_a.mp3', '/tmp/favorite_b.mp4']
    assert view.table.GetItemCount() == 0
    assert event_bus.published[-1][0] == AudioEventType.FEEDBACK_MESSAGE

    database_manager.items.append(
        {
            'path': '/tmp/favorite_c.mp3',
            'title': 'Charlie',
            'media_type': MediaType.AUDIO.value,
            'duration': 45.0,
            'metadata': {'artist': 'Charlie'},
        }
    )
    view._on_refresh()
    assert view.table.GetItemCount() == 1
    assert view.table.GetItemText(0, 0) == 'Charlie'



def test_wx_favorites_view_reacts_to_events_and_main_view_uses_real_page(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    view, database_manager, _player_controller, event_bus, _settings = build_favorites_view(monkeypatch)

    event_bus.publish(AudioEventType.PLAYER_STATE_CHANGED, {'current_track_path': '/tmp/favorite_b.mp4'})
    assert view._current_track_path == '/tmp/favorite_b.mp4'
    assert view.table.GetItemBackgroundColour(1) is not None
    assert view.table.GetItemBackgroundColour(0) != view.table.GetItemBackgroundColour(1)

    database_manager.items.insert(
        0,
        {
            'path': '/tmp/favorite_new.mp3',
            'title': 'Nova',
            'media_type': MediaType.AUDIO.value,
            'duration': 33.0,
            'metadata': {'artist': 'Nova'},
        },
    )
    event_bus.publish(AudioEventType.FAVORITE_CHANGED, {})
    assert view.table.GetItemCount() == 3
    assert view.table.GetItemText(0, 0) == 'Nova'

    main_view = MainView(
        FakeApp(),
        localization_manager=DummyLocalizationManager(),
        theme_manager=DummyThemeManager(),
        event_bus=DummyEventBus(),
        database_manager=DummyDatabaseManager(),
        player_controller=DummyPlayerController(),
    )
    favorites_page = main_view.get_view('favorites')
    assert getattr(favorites_page, 'owner', None).__class__.__name__ == 'FavoritesView'


def test_wx_favorites_view_updates_column_labels_after_runtime_language_switch(monkeypatch):
    view, _database_manager, _player_controller, _event_bus, _settings = build_favorites_view(monkeypatch)

    assert view.table.columns == ['Titolo', 'Artista', 'Durata', 'Percorso']

    view.localization_manager.set_mapping({
        'nav_favorites': 'Favoris',
        'favorites_header': 'Favoris',
        'favorites_col_title': 'Titre',
        'favorites_col_artist': 'Artiste',
        'favorites_col_duration': 'Durée',
        'favorites_col_path': 'Chemin',
        'favorites_status': 'Favoris : {count}',
        'favorites_remove_button': 'Supprimer la sélection',
        'favorites_select_all_button': 'Tout sélectionner',
        'library_play_selected_button': 'Lire la sélection',
        'library_refresh_button': 'Actualiser',
    })
    view.update_localization()

    assert view.table.columns == ['Titre', 'Artiste', 'Durée', 'Chemin']


class StrictFakeListItem:
    def __init__(self, text: str) -> None:
        self.text = str(text)

    def SetText(self, value: str) -> None:
        self.text = str(value)


class StrictFakeListCtrl(FakeWxModule.ListCtrl):
    def InsertColumn(self, index, heading, width=-1):
        insert_at = max(0, min(int(index), len(self.columns)))
        self.columns.insert(insert_at, str(heading))
        self.column_widths.insert(insert_at, width)
        for row in self.rows:
            row.insert(insert_at, '')

    def GetColumn(self, index):
        return StrictFakeListItem(self.columns[index])

    def SetColumn(self, index, item):
        if isinstance(item, str):
            raise TypeError('wx.ListItem required')
        self.columns[index] = str(getattr(item, 'text', ''))


def test_wx_favorites_view_runtime_language_switch_updates_real_columns_without_duplicates(monkeypatch):
    monkeypatch.setattr(FakeWxModule, 'ListCtrl', StrictFakeListCtrl)
    view, _database_manager, _player_controller, _event_bus, _settings = build_favorites_view(monkeypatch)

    assert view.table.columns == ['Titolo', 'Artista', 'Durata', 'Percorso']

    view.localization_manager.set_mapping({
        'nav_favorites': 'Favoris',
        'favorites_header': 'Favoris',
        'favorites_col_title': 'Titre',
        'favorites_col_artist': 'Artiste',
        'favorites_col_duration': 'Durée',
        'favorites_col_path': 'Chemin',
        'favorites_status': 'Favoris : {count}',
        'favorites_remove_button': 'Supprimer la sélection',
        'favorites_select_all_button': 'Tout sélectionner',
        'library_play_selected_button': 'Lire la sélection',
        'library_refresh_button': 'Actualiser',
    })
    view.update_localization()

    assert view.table.columns == ['Titre', 'Artiste', 'Durée', 'Chemin']
    assert len(view.table.columns) == 4


def test_wx_favorites_view_persists_column_widths_across_sessions(monkeypatch):
    settings = DummySettingsManager()
    view, _database_manager, _player, _event_bus, settings = build_favorites_view(monkeypatch, settings_manager=settings)

    view.table.SetColumnWidth(0, 260)
    view.table.SetColumnWidth(3, 500)
    view._on_table_column_resized(None)

    restored, _database_manager2, _player2, _event_bus2, _settings2 = build_favorites_view(monkeypatch, settings_manager=settings)
    assert restored.table.GetColumnWidth(0) == 260
    assert restored.table.GetColumnWidth(3) == 500


def test_wx_favorites_view_persists_column_widths_on_shutdown_without_drag_event(monkeypatch):
    settings = DummySettingsManager()
    view, _database_manager, _player, _event_bus, settings = build_favorites_view(monkeypatch, settings_manager=settings)

    view.table.SetColumnWidth(1, 277)
    view.table.SetColumnWidth(2, 144)
    view.shutdown()

    restored, _database_manager2, _player2, _event_bus2, _settings2 = build_favorites_view(monkeypatch, settings_manager=settings)
    assert restored.table.GetColumnWidth(1) == 277
    assert restored.table.GetColumnWidth(2) == 144
