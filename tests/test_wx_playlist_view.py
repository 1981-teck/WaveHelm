from __future__ import annotations

import sys
from dataclasses import dataclass

from src.audio.audio_event_models import AudioEventType
from src.model.media_file import MediaFile, MediaType
from src.ui_wx.main_view import MainView
from src.ui_wx.playlist_view import PlaylistView
from tests.wx_fakes import FakeApp, FakeDirDialog, FakeFileDialog, FakeTextEntryDialog, FakeWxModule


class DummyLocalizationManager:
    def __init__(self) -> None:
        self.language_callbacks = []
        self.mapping = {
            'playlist_tab': 'Playlist',
            'playlist_header': 'Playlist',
            'playlist_tracks_title': 'Brani',
            'playlist_btn_create': 'Crea playlist',
            'playlist_btn_delete': 'Elimina playlist',
            'playlist_btn_add_tracks': 'Aggiungi file',
            'playlist_btn_add_folder': 'Aggiungi cartella',
            'playlist_btn_remove_tracks': 'Rimuovi selezionati',
            'playlist_col_name': 'Nome',
            'playlist_col_count': '#',
            'playlist_col_title': 'Titolo',
            'playlist_col_artist': 'Artista',
            'playlist_col_duration': 'Durata',
            'playlist_col_path': 'Percorso',
            'playlist_create_title': 'Crea playlist',
            'playlist_create_prompt': 'Inserisci il nome della playlist:',
            'playlist_delete_confirm_title': 'Conferma eliminazione',
            'playlist_delete_confirm_message': "Sei sicuro di voler eliminare la playlist '{name}'?",
            'playlist_add_select_warning': 'Seleziona una playlist a cui aggiungere i brani.',
            'playlist_add_files_title': 'Seleziona i file da aggiungere',
            'playlist_add_folder_title': 'Seleziona una cartella con file multimediali',
            'playlist_add_folder_no_media': 'Nella cartella selezionata non sono stati trovati file multimediali riproducibili.',
            'playlist_delete_tracks_select_warning': 'Seleziona uno o piu brani da rimuovere dalla playlist.',
            'playlist_delete_tracks_confirm_title': 'Conferma',
            'playlist_delete_tracks_confirm_message': 'Sei sicuro di voler rimuovere {count} brani da questa playlist?',
            'playlist_created': "✅ Playlist '{name}' creata!",
            'playlist_deleted': "🗑️ Playlist '{name}' eliminata!",
            'playlist_creation_cancelled': 'Creazione playlist annullata o nome non valido.',
            'starting_playlist_playback': '▶ Avvio riproduzione di {count} brani dalla playlist.',
            'playlist_status': 'Playlist: {playlists} | Brani visibili: {tracks}',
            'library_play_selected_button': 'Riproduci selezionato',
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
        self.state_payload = {}

    def set_playback_context(self, items, start_index, autoplay):
        self.playback_contexts.append(DummyPlaybackContext(list(items), int(start_index), bool(autoplay)))

    def get_current_player_state_payload(self):
        return dict(self.state_payload)


class DummyPlaylistController:
    def __init__(self, event_bus: DummyEventBus) -> None:
        self.event_bus = event_bus
        self.playlists = [
            {'id': 1, 'name': 'Roadtrip'},
            {'id': 2, 'name': 'Night'},
        ]
        self.current_playlist_id = 1
        self.tracks = {
            1: [
                MediaFile('/tmp/a.mp3', title='Alpha', media_type=MediaType.AUDIO, duration=61.0, metadata={'artist': 'Able'}),
                MediaFile('/tmp/b.mp3', title='Beta', media_type=MediaType.AUDIO, duration=122.0, metadata={'artist': 'Baker'}),
            ],
            2: [
                MediaFile('/tmp/c.mp3', title='Charlie', media_type=MediaType.AUDIO, duration=75.0, metadata={'artist': 'Charlie'}),
            ],
        }
        self.added_batches = []
        self.removed_batches = []
        self.deleted_ids = []

    def get_all_playlists(self):
        return list(self.playlists)

    def get_playlist_track_count(self, playlist_id):
        return len(self.tracks.get(int(playlist_id), []))

    def get_tracks_in_playlist(self, playlist_id):
        return list(self.tracks.get(int(playlist_id), []))

    def set_current_playlist(self, playlist_id):
        self.current_playlist_id = int(playlist_id)

    def create_playlist(self, name):
        playlist_id = max(item['id'] for item in self.playlists) + 1
        self.playlists.append({'id': playlist_id, 'name': name})
        self.tracks[playlist_id] = []
        self.current_playlist_id = playlist_id
        self.event_bus.publish(AudioEventType.PLAYLIST_UPDATED, {'id': playlist_id, 'name': name})
        return playlist_id

    def delete_playlist(self, playlist_id):
        playlist_id = int(playlist_id)
        self.deleted_ids.append(playlist_id)
        self.playlists = [item for item in self.playlists if int(item['id']) != playlist_id]
        self.tracks.pop(playlist_id, None)
        self.event_bus.publish(AudioEventType.PLAYLIST_UPDATED, {'deleted_id': playlist_id})

    def add_files_to_current_playlist(self, file_paths):
        playlist_id = self.current_playlist_id
        self.added_batches.append((playlist_id, list(file_paths)))
        target = self.tracks.setdefault(playlist_id, [])
        for path in file_paths:
            target.append(MediaFile(str(path), title=str(path).split('/')[-1].split('.')[0].title(), media_type=MediaType.AUDIO, duration=33.0, metadata={'artist': 'Imported'}))
        self.event_bus.publish(AudioEventType.PLAYLIST_UPDATED, {'id': playlist_id})

    def remove_many_from_playlist(self, playlist_id, media_paths):
        playlist_id = int(playlist_id)
        self.removed_batches.append((playlist_id, list(media_paths)))
        before = len(self.tracks.get(playlist_id, []))
        keep = [item for item in self.tracks.get(playlist_id, []) if item.path not in set(media_paths)]
        self.tracks[playlist_id] = keep
        removed = before - len(keep)
        self.event_bus.publish(AudioEventType.PLAYLIST_UPDATED, {'id': playlist_id})
        return removed



def build_playlist_view(monkeypatch, settings_manager=None):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    FakeWxModule.next_message_box_result = FakeWxModule.YES
    FakeTextEntryDialog.next_result = FakeWxModule.ID_OK
    FakeTextEntryDialog.next_value = ''
    FakeFileDialog.next_result = FakeWxModule.ID_OK
    FakeFileDialog.next_paths = []
    FakeDirDialog.next_result = FakeWxModule.ID_OK
    FakeDirDialog.next_paths = []
    if settings_manager is None:
        settings_manager = DummySettingsManager()
    event_bus = DummyEventBus()
    playlist_controller = DummyPlaylistController(event_bus)
    player_controller = DummyPlayerController()
    view = PlaylistView(
        FakeWxModule.Panel(None),
        playlist_controller=playlist_controller,
        player_controller=player_controller,
        localization_manager=DummyLocalizationManager(),
        theme_manager=DummyThemeManager(),
        event_bus=event_bus,
        settings_manager=settings_manager,
    )
    return view, playlist_controller, player_controller, event_bus, settings_manager



def test_wx_playlist_view_populates_tables_and_labels(monkeypatch):
    view, playlist_controller, _player, _event_bus, _settings = build_playlist_view(monkeypatch)

    assert view.title_label.label == 'Playlist'
    assert view.playlist_table.columns == ['Nome', '#']
    assert view.track_table.columns == ['Titolo', 'Artista', 'Durata', 'Percorso']
    assert view.playlist_table.GetItemCount() == 2
    assert view.track_table.GetItemCount() == 2
    assert view.playlist_table.GetItemText(0, 0) == 'Roadtrip'
    assert view.track_table.GetItemText(1, 0) == 'Beta'
    assert playlist_controller.current_playlist_id == 1
    assert view.status_label.label == 'Playlist: 2 | Brani visibili: 2'
    assert view.add_folder_button.label == 'Aggiungi cartella'



def test_wx_playlist_view_create_delete_add_remove_and_play(monkeypatch):
    view, playlist_controller, player_controller, event_bus, _settings = build_playlist_view(monkeypatch)

    FakeTextEntryDialog.next_value = 'Focus'
    view._on_create_playlist()
    assert any(item['name'] == 'Focus' for item in playlist_controller.playlists)
    assert view.playlist_table.GetItemText(2, 0) == 'Focus'

    view.playlist_table.Select(0, True)
    view._on_playlist_selected()
    FakeFileDialog.next_paths = ['/tmp/new_one.mp3', '/tmp/new_two.mp3']
    view._on_add_tracks()
    assert playlist_controller.added_batches[-1] == (1, ['/tmp/new_one.mp3', '/tmp/new_two.mp3'])
    assert view.track_table.GetItemCount() == 4

    view.track_table.Select(1, True)
    view._on_play_selected()
    assert player_controller.playback_contexts[-1].start_index == 1
    assert player_controller.playback_contexts[-1].autoplay is True

    view.track_table.Select(0, True)
    view.track_table.Select(1, True)
    view._on_remove_tracks()
    assert playlist_controller.removed_batches[-1] == (1, ['/tmp/a.mp3', '/tmp/b.mp3'])
    assert event_bus.published[-1][0] == AudioEventType.FEEDBACK_MESSAGE

    view.playlist_table.Select(0, False)
    view.playlist_table.Select(2, True)
    view._on_playlist_selected()
    view._on_delete_playlist()
    assert 3 in playlist_controller.deleted_ids



def test_wx_playlist_view_add_folder_collects_media_files(monkeypatch, tmp_path):
    view, playlist_controller, _player_controller, _event_bus, _settings = build_playlist_view(monkeypatch)

    media_dir = tmp_path / 'album'
    nested_dir = media_dir / 'disc1'
    nested_dir.mkdir(parents=True)
    (media_dir / 'cover.txt').write_text('ignore', encoding='utf-8')
    first = media_dir / 'song_a.mp3'
    second = nested_dir / 'clip_b.mp4'
    first.write_bytes(b'')
    second.write_bytes(b'')

    view.playlist_table.Select(0, True)
    view._on_playlist_selected()
    FakeDirDialog.next_paths = [str(media_dir)]

    view._on_add_folder()

    added_playlist_id, added_paths = playlist_controller.added_batches[-1]
    assert added_playlist_id == 1
    assert added_paths == [str(nested_dir / 'clip_b.mp4'), str(media_dir / 'song_a.mp3')]
    assert view.track_table.GetItemCount() == 4



def test_wx_playlist_view_reacts_to_playlist_selection_and_player_state(monkeypatch):
    view, _playlist_controller, _player_controller, event_bus, _settings = build_playlist_view(monkeypatch)

    view.playlist_table.Select(1, True)
    view._on_playlist_selected()
    assert view.track_table.GetItemCount() == 1
    assert view.track_table.GetItemText(0, 0) == 'Charlie'

    event_bus.publish(AudioEventType.PLAYER_STATE_CHANGED, {'current_track_path': '/tmp/c.mp3'})
    assert view._current_track_path == '/tmp/c.mp3'
    assert view.track_table.GetItemBackgroundColour(0) is not None



def test_wx_main_view_uses_real_playlist_page(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    root = FakeApp()
    event_bus = DummyEventBus()
    main_view = MainView(
        root,
        localization_manager=DummyLocalizationManager(),
        theme_manager=DummyThemeManager(),
        event_bus=event_bus,
        playlist_controller=DummyPlaylistController(event_bus),
        player_controller=DummyPlayerController(),
    )

    playlist_panel = main_view.get_view('playlist')

    assert playlist_panel is not None
    assert getattr(playlist_panel, 'owner', None).__class__.__name__ == 'PlaylistView'


def test_wx_playlist_view_updates_column_labels_after_runtime_language_switch(monkeypatch):
    view, _playlist_controller, _player, _event_bus, _settings = build_playlist_view(monkeypatch)

    assert view.playlist_table.columns == ['Nome', '#']
    assert view.track_table.columns == ['Titolo', 'Artista', 'Durata', 'Percorso']

    view.localization_manager.set_mapping({
        'playlist_tab': 'Lista de reproducción',
        'playlist_header': 'Listas',
        'playlist_tracks_title': 'Pistas',
        'playlist_btn_create': 'Crear lista',
        'playlist_btn_delete': 'Eliminar lista',
        'playlist_btn_add_tracks': 'Añadir archivos',
        'playlist_btn_add_folder': 'Añadir carpeta',
        'playlist_btn_remove_tracks': 'Eliminar seleccionados',
        'playlist_col_name': 'Nombre',
        'playlist_col_count': '#',
        'playlist_col_title': 'Título',
        'playlist_col_artist': 'Artista',
        'playlist_col_duration': 'Duración',
        'playlist_col_path': 'Ruta',
        'playlist_status': 'Listas: {playlists} | Pistas visibles: {tracks}',
        'library_play_selected_button': 'Reproducir seleccionados',
    })
    view.update_localization()

    assert view.playlist_table.columns == ['Nombre', '#']
    assert view.track_table.columns == ['Título', 'Artista', 'Duración', 'Ruta']


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


def test_wx_playlist_view_runtime_language_switch_updates_real_columns_without_duplicates(monkeypatch):
    monkeypatch.setattr(FakeWxModule, 'ListCtrl', StrictFakeListCtrl)
    view, _playlist_controller, _player, _event_bus, _settings = build_playlist_view(monkeypatch)

    assert view.playlist_table.columns == ['Nome', '#']
    assert view.track_table.columns == ['Titolo', 'Artista', 'Durata', 'Percorso']

    view.localization_manager.set_mapping({
        'playlist_tab': 'Lista de reproducción',
        'playlist_header': 'Listas',
        'playlist_tracks_title': 'Pistas',
        'playlist_btn_create': 'Crear lista',
        'playlist_btn_delete': 'Eliminar lista',
        'playlist_btn_add_tracks': 'Añadir archivos',
        'playlist_btn_add_folder': 'Añadir carpeta',
        'playlist_btn_remove_tracks': 'Eliminar seleccionados',
        'playlist_col_name': 'Nombre',
        'playlist_col_count': '#',
        'playlist_col_title': 'Título',
        'playlist_col_artist': 'Artista',
        'playlist_col_duration': 'Duración',
        'playlist_col_path': 'Ruta',
        'playlist_status': 'Listas: {playlists} | Pistas visibles: {tracks}',
        'library_play_selected_button': 'Reproducir seleccionados',
    })
    view.update_localization()

    assert view.playlist_table.columns == ['Nombre', '#']
    assert view.track_table.columns == ['Título', 'Artista', 'Duración', 'Ruta']
    assert len(view.playlist_table.columns) == 2
    assert len(view.track_table.columns) == 4


def test_wx_playlist_view_persists_column_widths_across_sessions(monkeypatch):
    settings = DummySettingsManager()
    view, _playlist_controller, _player, _event_bus, settings = build_playlist_view(monkeypatch, settings_manager=settings)

    view.playlist_table.SetColumnWidth(0, 210)
    view.track_table.SetColumnWidth(3, 520)
    view._on_playlist_table_column_resized(None)
    view._on_track_table_column_resized(None)

    restored, _playlist_controller2, _player2, _event_bus2, _settings2 = build_playlist_view(monkeypatch, settings_manager=settings)
    assert restored.playlist_table.GetColumnWidth(0) == 210
    assert restored.track_table.GetColumnWidth(3) == 520


def test_wx_playlist_view_persists_column_widths_on_shutdown_without_drag_event(monkeypatch):
    settings = DummySettingsManager()
    view, _playlist_controller, _player, _event_bus, settings = build_playlist_view(monkeypatch, settings_manager=settings)

    view.playlist_table.SetColumnWidth(1, 188)
    view.track_table.SetColumnWidth(2, 301)
    view.shutdown()

    restored, _playlist_controller2, _player2, _event_bus2, _settings2 = build_playlist_view(monkeypatch, settings_manager=settings)
    assert restored.playlist_table.GetColumnWidth(1) == 188
    assert restored.track_table.GetColumnWidth(2) == 301
