from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from typing import Any

from src.audio.audio_event_models import AudioEventType
from src.model.media_file import MediaFile
from src.utils.helpers import is_audio_file, is_video_file
from src.ui_wx.common import (
    WX_CALLBACK_EXCEPTIONS,
    apply_colors,
    create_flow_sizer,
    format_duration,
    refresh_now_playing_highlight,
    set_view_feedback,
    subscribe_event,
    get_localized_text,
    get_theme_colors,
    persist_listctrl_column_widths,
    register_callback,
    restore_listctrl_column_widths,
    set_label_text,
    set_listctrl_column_label,
    unregister_callback,
)

logger = logging.getLogger(__name__)

PLAYLIST_VIEW_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
_DIALOG_CANCELLED = {0, -1}


class PlaylistView:
    """wx-based playlist management page.

    Edge cases handled deterministically:
    1. Playlist and track selections can become stale after delete/update events, so every action re-resolves the current rows.
    2. Dialog APIs or controller capabilities may be absent on a partial runtime, so actions degrade to bounded feedback instead of a broken flow.
    3. Player state events can reference paths that are no longer rendered, so highlight refresh is best-effort and never crashes the page.
    """

    FEEDBACK_EVENT_TYPE = AudioEventType.FEEDBACK_MESSAGE
    _set_feedback = set_view_feedback

    PLAYLIST_COLUMN_WIDTHS_SETTING_KEY = 'ui_playlist_playlist_column_widths'
    TRACK_COLUMN_WIDTHS_SETTING_KEY = 'ui_playlist_track_column_widths'

    PLAYLIST_COLUMNS: tuple[tuple[str, str], ...] = (
        ('name', 'playlist_col_name'),
        ('count', 'playlist_col_count'),
    )
    TRACK_COLUMNS: tuple[tuple[str, str], ...] = (
        ('title', 'playlist_col_title'),
        ('artist', 'playlist_col_artist'),
        ('duration', 'playlist_col_duration'),
        ('path', 'playlist_col_path'),
    )

    def __init__(
        self,
        parent: Any,
        playlist_controller: Any = None,
        player_controller: Any = None,
        localization_manager: Any = None,
        theme_manager: Any = None,
        event_bus: Any = None,
        settings_manager: Any = None,
        **_: Any,
    ) -> None:
        self._wx = self._import_wx_module()
        self.playlist_controller = playlist_controller
        self.player_controller = player_controller
        self.localization_manager = localization_manager
        self.theme_manager = theme_manager
        self.event_bus = event_bus
        self.settings_manager = settings_manager
        self.panel = self._wx.Panel(parent)
        self.panel.owner = self
        self._subscriptions: list[tuple[AudioEventType, Any]] = []
        self._playlists: list[dict[str, Any]] = []
        self._current_playlist_id: int | None = None
        self._current_playlist_tracks: list[MediaFile] = []
        self._current_track_path: str = ''

        self.title_label: Any = None
        self.status_label: Any = None
        self.feedback_label: Any = None
        self.playlist_label: Any = None
        self.track_label: Any = None
        self.playlist_table: Any = None
        self.track_table: Any = None
        self.create_button: Any = None
        self.delete_button: Any = None
        self.add_tracks_button: Any = None
        self.add_folder_button: Any = None
        self.remove_tracks_button: Any = None
        self.play_button: Any = None

        self._build_ui()
        self._bind_events()
        self._register_callbacks()
        self._subscribe_to_events()
        self.reload_playlists()
        self.update_localization()
        self.update_theme_colors()

    @staticmethod
    def _import_wx_module() -> Any:
        try:
            return importlib.import_module('wx')
        except ImportError as exc:
            raise RuntimeError('wx is required to instantiate the wx PlaylistView.') from exc

    def _build_ui(self) -> None:
        wx = self._wx
        root = wx.BoxSizer(wx.VERTICAL)
        self.title_label = wx.StaticText(self.panel, label='')
        root.Add(self.title_label, 0, wx.ALL | wx.EXPAND, 10)
        root.Add(self._build_toolbar(), 0, wx.ALL | wx.EXPAND, 0)
        root.Add(self._build_tables_area(), 1, wx.ALL | wx.EXPAND, 0)
        self.status_label = wx.StaticText(self.panel, label='')
        self.feedback_label = wx.StaticText(self.panel, label='')
        root.Add(self.status_label, 0, wx.ALL | wx.EXPAND, 8)
        root.Add(self.feedback_label, 0, wx.ALL | wx.EXPAND, 8)
        self.panel.SetSizer(root)

    def _build_toolbar(self) -> Any:
        wx = self._wx
        row = create_flow_sizer(wx)
        self.create_button = wx.Button(self.panel, label='')
        self.delete_button = wx.Button(self.panel, label='')
        self.add_tracks_button = wx.Button(self.panel, label='')
        self.add_folder_button = wx.Button(self.panel, label='')
        self.remove_tracks_button = wx.Button(self.panel, label='')
        self.play_button = wx.Button(self.panel, label='')
        for button in (
            self.create_button,
            self.delete_button,
            self.add_tracks_button,
            self.add_folder_button,
            self.remove_tracks_button,
            self.play_button,
        ):
            row.Add(button, 0, wx.ALL | wx.EXPAND, 6)
        return row

    def _build_tables_area(self) -> Any:
        wx = self._wx
        wrapper = wx.BoxSizer(wx.HORIZONTAL)
        wrapper.Add(self._build_playlist_column(), 0, wx.ALL | wx.EXPAND, 6)
        wrapper.Add(self._build_tracks_column(), 1, wx.ALL | wx.EXPAND, 6)
        return wrapper

    def _build_playlist_column(self) -> Any:
        wx = self._wx
        column = wx.BoxSizer(wx.VERTICAL)
        self.playlist_label = wx.StaticText(self.panel, label='')
        self.playlist_table = wx.ListCtrl(self.panel, style=getattr(wx, 'LC_REPORT', 0))
        for index, (name, key) in enumerate(self.PLAYLIST_COLUMNS):
            self.playlist_table.InsertColumn(index, self._t(key, name.title()))
        column.Add(self.playlist_label, 0, wx.ALL | wx.EXPAND, 4)
        column.Add(self.playlist_table, 1, wx.ALL | wx.EXPAND, 0)
        return column

    def _build_tracks_column(self) -> Any:
        wx = self._wx
        column = wx.BoxSizer(wx.VERTICAL)
        self.track_label = wx.StaticText(self.panel, label='')
        self.track_table = wx.ListCtrl(self.panel, style=getattr(wx, 'LC_REPORT', 0))
        for index, (name, key) in enumerate(self.TRACK_COLUMNS):
            self.track_table.InsertColumn(index, self._t(key, name.title()))
        column.Add(self.track_label, 0, wx.ALL | wx.EXPAND, 4)
        column.Add(self.track_table, 1, wx.ALL | wx.EXPAND, 0)
        return column

    def _bind_events(self) -> None:
        wx = self._wx
        self.create_button.Bind(wx.EVT_BUTTON, self._on_create_playlist)
        self.delete_button.Bind(wx.EVT_BUTTON, self._on_delete_playlist)
        self.add_tracks_button.Bind(wx.EVT_BUTTON, self._on_add_tracks)
        self.add_folder_button.Bind(wx.EVT_BUTTON, self._on_add_folder)
        self.remove_tracks_button.Bind(wx.EVT_BUTTON, self._on_remove_tracks)
        self.play_button.Bind(wx.EVT_BUTTON, self._on_play_selected)
        selected_event = getattr(wx, 'EVT_LIST_ITEM_SELECTED', None)
        if selected_event is not None:
            self.playlist_table.Bind(selected_event, self._on_playlist_selected)
            self.track_table.Bind(selected_event, self._on_track_selection_changed)
        self.track_table.Bind(getattr(wx, 'EVT_LIST_ITEM_ACTIVATED', ''), self._on_track_activated)
        column_resize_event = getattr(wx, 'EVT_LIST_COL_END_DRAG', None)
        if column_resize_event is not None:
            self.playlist_table.Bind(column_resize_event, self._on_playlist_table_column_resized)
            self.track_table.Bind(column_resize_event, self._on_track_table_column_resized)

    def _register_callbacks(self) -> None:
        register_callback(
            self.localization_manager,
            'register_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to register wx PlaylistView language callback.',
        )
        register_callback(
            self.theme_manager,
            'register_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to register wx PlaylistView theme callback.',
        )

    def _subscribe_to_events(self) -> None:
        self._subscribe(AudioEventType.PLAYLIST_UPDATED, self._on_playlist_updated)
        self._subscribe(AudioEventType.PLAYER_STATE_CHANGED, self._on_player_state_changed)

    def _subscribe(self, event_type: AudioEventType, callback: Any) -> None:
        subscribe_event(
            self.event_bus,
            self._subscriptions,
            event_type,
            callback,
            exceptions=PLAYLIST_VIEW_EXCEPTIONS,
            logger=logger,
            view_name='PlaylistView',
        )

    def _t(self, key: str, default: str | None = None, **kwargs: Any) -> str:
        fallback = default if default is not None else key
        return get_localized_text(self.localization_manager, key, fallback, **kwargs)

    def update_localization(self, *_: Any) -> None:
        set_label_text(self.title_label, self._t('nav_playlist', self._t('playlist_tab', 'Playlist')))
        set_label_text(self.playlist_label, self._t('playlist_header', 'Playlists'))
        set_label_text(self.track_label, self._t('playlist_tracks_title', 'Tracks'))
        set_label_text(self.create_button, self._t('playlist_btn_create', 'Create playlist'))
        set_label_text(self.delete_button, self._t('playlist_btn_delete', 'Delete playlist'))
        set_label_text(self.add_tracks_button, self._t('playlist_btn_add_tracks', 'Add files'))
        set_label_text(self.add_folder_button, self._t('playlist_btn_add_folder', 'Add folder'))
        set_label_text(self.remove_tracks_button, self._t('playlist_btn_remove_tracks', 'Remove selected'))
        set_label_text(self.play_button, self._t('library_play_selected_button', 'Play selected'))
        self._set_table_labels()
        self._restore_column_widths()
        self._update_status_label()

    def _set_table_labels(self) -> None:
        playlist_columns = getattr(self.playlist_table, 'columns', None)
        for index, (name, key) in enumerate(self.PLAYLIST_COLUMNS):
            label = self._t(key, name.title())
            set_listctrl_column_label(self.playlist_table, index, label)
        track_columns = getattr(self.track_table, 'columns', None)
        for index, (name, key) in enumerate(self.TRACK_COLUMNS):
            label = self._t(key, name.title())
            set_listctrl_column_label(self.track_table, index, label)

    def _restore_column_widths(self) -> None:
        restore_listctrl_column_widths(
            self.playlist_table,
            self.settings_manager,
            self.PLAYLIST_COLUMN_WIDTHS_SETTING_KEY,
            len(self.PLAYLIST_COLUMNS),
        )
        restore_listctrl_column_widths(
            self.track_table,
            self.settings_manager,
            self.TRACK_COLUMN_WIDTHS_SETTING_KEY,
            len(self.TRACK_COLUMNS),
        )

    def _persist_playlist_column_widths(self) -> None:
        persist_listctrl_column_widths(
            self.playlist_table,
            self.settings_manager,
            self.PLAYLIST_COLUMN_WIDTHS_SETTING_KEY,
            len(self.PLAYLIST_COLUMNS),
        )

    def _persist_track_column_widths(self) -> None:
        persist_listctrl_column_widths(
            self.track_table,
            self.settings_manager,
            self.TRACK_COLUMN_WIDTHS_SETTING_KEY,
            len(self.TRACK_COLUMNS),
        )

    def _on_playlist_table_column_resized(self, _event: Any) -> None:
        self._persist_playlist_column_widths()

    def _on_track_table_column_resized(self, _event: Any) -> None:
        self._persist_track_column_widths()


    def update_theme_colors(self, *_: Any) -> None:
        colors = get_theme_colors(self.theme_manager)
        background = colors.get('panel_bg') or colors.get('bg_color')
        foreground = colors.get('text_color')
        accent = colors.get('button_color') or background
        for widget in (
            self.panel,
            self.title_label,
            self.playlist_label,
            self.track_label,
            self.playlist_table,
            self.track_table,
            self.status_label,
            self.feedback_label,
        ):
            apply_colors(widget, background=background, foreground=foreground)
        for button in (
            self.create_button,
            self.delete_button,
            self.add_tracks_button,
            self.add_folder_button,
            self.remove_tracks_button,
            self.play_button,
        ):
            apply_colors(button, background=accent, foreground=foreground)
        self._refresh_now_playing_highlight(colors)

    def reload_playlists(self) -> None:
        self._playlists = self._get_all_playlists()
        if self._current_playlist_id is not None and not any(self._playlist_id(item) == self._current_playlist_id for item in self._playlists):
            self._current_playlist_id = None
        self._populate_playlist_rows()
        if self._current_playlist_id is None and self._playlists:
            self._current_playlist_id = self._playlist_id(self._playlists[0])
        self._load_tracks_for_current_playlist()
        self._update_status_label()

    def _get_all_playlists(self) -> list[dict[str, Any]]:
        getter = getattr(self.playlist_controller, 'get_all_playlists', None)
        if not callable(getter):
            return []
        try:
            items = getter()
        except PLAYLIST_VIEW_EXCEPTIONS:
            logger.debug('Unable to read playlists.', exc_info=True)
            return []
        return [item for item in items if isinstance(item, dict)]

    def _playlist_id(self, playlist: dict[str, Any]) -> int | None:
        try:
            return int(playlist.get('id'))
        except PLAYLIST_VIEW_EXCEPTIONS:
            return None

    def _playlist_name(self, playlist: dict[str, Any]) -> str:
        value = playlist.get('name')
        return str(value).strip() if value else ''

    def _populate_playlist_rows(self) -> None:
        self.playlist_table.DeleteAllItems()
        for row_index, playlist in enumerate(self._playlists):
            name = self._playlist_name(playlist) or self._t('playlist_empty', 'Playlist')
            inserted = self.playlist_table.InsertItem(row_index, name)
            self.playlist_table.SetItem(inserted, 1, str(self._playlist_track_count(playlist)))
            if self._playlist_id(playlist) == self._current_playlist_id:
                self.playlist_table.Select(inserted, True)

    def _playlist_track_count(self, playlist: dict[str, Any]) -> int:
        getter = getattr(self.playlist_controller, 'get_playlist_track_count', None)
        playlist_id = self._playlist_id(playlist)
        if not callable(getter) or playlist_id is None:
            return 0
        try:
            return int(getter(playlist_id) or 0)
        except PLAYLIST_VIEW_EXCEPTIONS:
            logger.debug('Unable to read playlist track count.', exc_info=True)
            return 0

    def _load_tracks_for_current_playlist(self) -> None:
        self.track_table.DeleteAllItems()
        playlist_id = self._current_playlist_id
        if playlist_id is None:
            self._current_playlist_tracks = []
            self._update_status_label()
            return
        getter = getattr(self.playlist_controller, 'get_tracks_in_playlist', None)
        if not callable(getter):
            self._current_playlist_tracks = []
            self._update_status_label()
            return
        try:
            items = getter(playlist_id)
        except PLAYLIST_VIEW_EXCEPTIONS:
            logger.debug('Unable to load playlist tracks.', exc_info=True)
            items = []
        self._current_playlist_tracks = [item for item in items if isinstance(item, MediaFile)]
        self._populate_track_rows()
        self._update_status_label()

    def _populate_track_rows(self) -> None:
        self.track_table.DeleteAllItems()
        for row_index, media in enumerate(self._current_playlist_tracks):
            values = self._track_row_values(media)
            inserted = self.track_table.InsertItem(row_index, values[0])
            for column_index, value in enumerate(values[1:], start=1):
                self.track_table.SetItem(inserted, column_index, value)
        self._refresh_now_playing_highlight()

    @staticmethod
    def _canonical_path(path: str) -> str:
        value = str(path or '').strip()
        if not value:
            return ''
        if value.startswith(('http://', 'https://', 'rtsp://', 'rtmp://')):
            return value
        try:
            return os.path.normcase(os.path.abspath(os.path.normpath(value)))
        except PLAYLIST_VIEW_EXCEPTIONS:
            return value


    def _refresh_now_playing_highlight(self, colors: dict[str, str] | None = None) -> None:
        refresh_now_playing_highlight(
            self.track_table,
            self._current_playlist_tracks,
            self._current_track_path,
            self._canonical_path,
            self.theme_manager,
            colors=colors,
            exceptions=PLAYLIST_VIEW_EXCEPTIONS,
            logger=logger,
            view_name='PlaylistView',
            table_name='track table',
        )

    def _track_row_values(self, media: MediaFile) -> list[str]:
        metadata = dict(getattr(media, 'metadata', {}) or {})
        artist = str(getattr(media, 'artist', None) or metadata.get('artist', '') or '')
        return [
            getattr(media, 'title', '') or Path(getattr(media, 'path', '')).stem,
            artist,
            format_duration(float(getattr(media, 'duration', 0.0) or 0.0)),
            getattr(media, 'path', '') or '',
        ]


    def _selected_playlist(self) -> dict[str, Any] | None:
        index = self.playlist_table.GetFirstSelected()
        if index == -1 or index >= len(self._playlists):
            return None
        return self._playlists[index]

    def _selected_track_paths(self) -> list[str]:
        selected: list[str] = []
        index = self.track_table.GetFirstSelected()
        while index != -1:
            if 0 <= index < len(self._current_playlist_tracks):
                path = getattr(self._current_playlist_tracks[index], 'path', '') or ''
                if path:
                    selected.append(path)
            index = self.track_table.GetNextSelected(index)
        return selected

    def _selected_tracks(self) -> list[MediaFile]:
        selected: list[MediaFile] = []
        index = self.track_table.GetFirstSelected()
        while index != -1:
            if 0 <= index < len(self._current_playlist_tracks):
                selected.append(self._current_playlist_tracks[index])
            index = self.track_table.GetNextSelected(index)
        return selected

    def _on_playlist_selected(self, _event: Any | None = None) -> None:
        playlist = self._selected_playlist()
        if playlist is None:
            return
        playlist_id = self._playlist_id(playlist)
        if playlist_id is None or playlist_id == self._current_playlist_id:
            return
        self._current_playlist_id = playlist_id
        setter = getattr(self.playlist_controller, 'set_current_playlist', None)
        if callable(setter):
            try:
                setter(playlist_id)
            except PLAYLIST_VIEW_EXCEPTIONS:
                logger.debug('Unable to set current playlist.', exc_info=True)
        self._load_tracks_for_current_playlist()

    def _on_track_selection_changed(self, _event: Any | None = None) -> None:
        self._apply_player_highlight(getattr(self.player_controller, 'get_current_player_state_payload', lambda: {})())

    def _on_track_activated(self, _event: Any | None = None) -> None:
        self._on_play_selected()

    def _on_play_selected(self, _event: Any | None = None) -> None:
        selected_tracks = self._selected_tracks()
        if not selected_tracks:
            self._set_feedback(self._t('select_playlist_to_play', 'Select a playlist item to play.'), 'orange')
            return
        selected = selected_tracks[0]
        start_index = self._current_playlist_tracks.index(selected) if selected in self._current_playlist_tracks else 0
        setter = getattr(self.player_controller, 'set_playback_context', None)
        if not callable(setter):
            self._set_feedback(self._t('library_playback_unavailable', 'Playback is not available.'), 'orange')
            return
        setter(items=list(self._current_playlist_tracks), start_index=start_index, autoplay=True)
        self._set_feedback(
            self._t('starting_playlist_playback', '▶ Starting playback of {count} tracks from playlist.', count=len(self._current_playlist_tracks)),
            'green',
        )

    def _on_create_playlist(self, _event: Any | None = None) -> None:
        name = self._prompt_playlist_name()
        if not name:
            self._set_feedback(self._t('playlist_creation_cancelled', 'Playlist creation cancelled or invalid name.'), 'orange')
            return
        creator = getattr(self.playlist_controller, 'create_playlist', None)
        if not callable(creator):
            self._set_feedback(self._t('playlist_creation_error', 'Error creating playlist: unavailable', error='unavailable'), 'orange')
            return
        try:
            playlist_id = creator(name)
        except PLAYLIST_VIEW_EXCEPTIONS as exc:
            logger.debug('Unable to create playlist.', exc_info=True)
            self._set_feedback(self._t('playlist_creation_error', 'Error creating playlist: {error}', error=str(exc)), 'orange')
            return
        if playlist_id is None:
            self._set_feedback(self._t('playlist_creation_cancelled', 'Playlist creation cancelled or invalid name.'), 'orange')
            return
        self._current_playlist_id = int(playlist_id)
        self.reload_playlists()
        self._set_feedback(self._t('playlist_created', "✅ Playlist '{name}' created!", name=name), 'green')

    def _on_delete_playlist(self, _event: Any | None = None) -> None:
        playlist = self._selected_playlist()
        if playlist is None:
            self._set_feedback(self._t('playlist_delete_select_warning', 'Select a playlist to delete.'), 'orange')
            return
        playlist_id = self._playlist_id(playlist)
        playlist_name = self._playlist_name(playlist) or self._t('playlist_tab', 'Playlist')
        if playlist_id is None:
            self._set_feedback(self._t('playlist_not_found_delete', 'Playlist not found for deletion.'), 'orange')
            return
        confirmed = self._confirm(
            self._t('playlist_delete_confirm_title', 'Confirm deletion'),
            self._t('playlist_delete_confirm_message', "Are you sure you want to delete playlist '{name}'?", name=playlist_name),
        )
        if not confirmed:
            self._set_feedback(self._t('playlist_delete_cancelled', 'Playlist deletion cancelled.'), 'orange')
            return
        deleter = getattr(self.playlist_controller, 'delete_playlist', None)
        if not callable(deleter):
            self._set_feedback(self._t('playlist_delete_error', '❗ Error deleting playlist: {error}', error='unavailable'), 'orange')
            return
        try:
            deleter(playlist_id)
        except PLAYLIST_VIEW_EXCEPTIONS as exc:
            logger.debug('Unable to delete playlist.', exc_info=True)
            self._set_feedback(self._t('playlist_delete_error', '❗ Error deleting playlist: {error}', error=str(exc)), 'orange')
            return
        if playlist_id == self._current_playlist_id:
            self._current_playlist_id = None
        self.reload_playlists()
        self._set_feedback(self._t('playlist_deleted', "🗑️ Playlist '{name}' deleted!", name=playlist_name), 'green')

    def _on_add_tracks(self, _event: Any | None = None) -> None:
        playlist_id = self._selected_playlist_id_for_add()
        if playlist_id is None:
            return
        file_paths = self._select_file_paths()
        if not file_paths:
            return
        if self._add_paths_to_playlist(playlist_id, file_paths):
            self._set_feedback(self._t('tracks_added_to_playlist', 'Tracks added to playlist.', count=len(file_paths)), 'green')

    def _on_add_folder(self, _event: Any | None = None) -> None:
        playlist_id = self._selected_playlist_id_for_add()
        if playlist_id is None:
            return
        folder_path = self._select_folder_path()
        if not folder_path:
            return
        media_paths = self._collect_media_file_paths(folder_path)
        if not media_paths:
            self._set_feedback(self._t('playlist_add_folder_no_media', 'No playable media files were found in the selected folder.'), 'orange')
            return
        if self._add_paths_to_playlist(playlist_id, media_paths):
            self._set_feedback(self._t('tracks_added_to_playlist', 'Tracks added to playlist.', count=len(media_paths)), 'green')

    def _selected_playlist_id_for_add(self) -> int | None:
        playlist = self._selected_playlist()
        if playlist is None:
            self._set_feedback(self._t('playlist_add_select_warning', 'Select a playlist to add tracks to.'), 'orange')
            return None
        playlist_id = self._playlist_id(playlist)
        if playlist_id is None:
            self._set_feedback(self._t('playlist_add_select_warning', 'Select a playlist to add tracks to.'), 'orange')
            return None
        return playlist_id

    def _add_paths_to_playlist(self, playlist_id: int, file_paths: list[str]) -> bool:
        setter = getattr(self.playlist_controller, 'set_current_playlist', None)
        adder = getattr(self.playlist_controller, 'add_files_to_current_playlist', None)
        if callable(setter):
            try:
                setter(playlist_id)
            except PLAYLIST_VIEW_EXCEPTIONS:
                logger.debug('Unable to set current playlist before add.', exc_info=True)
        if not callable(adder):
            self._set_feedback(self._t('playlist_add_not_available', 'Add to playlist not available.'), 'orange')
            return False
        self._current_playlist_id = playlist_id
        try:
            adder(file_paths)
        except PLAYLIST_VIEW_EXCEPTIONS:
            logger.debug('Unable to add files to playlist.', exc_info=True)
            self._set_feedback(self._t('playlist_creation_error', 'Error creating playlist: {error}', error='add failed'), 'orange')
            return False
        self.reload_playlists()
        return True

    def _on_remove_tracks(self, _event: Any | None = None) -> None:
        playlist_id = self._current_playlist_id
        selected_paths = self._selected_track_paths()
        if playlist_id is None:
            self._set_feedback(self._t('no_playlist_selected_for_track_removal', 'No playlist selected for track removal.'), 'orange')
            return
        if not selected_paths:
            self._set_feedback(self._t('playlist_delete_tracks_select_warning', 'Select one or more tracks to remove from the playlist.'), 'orange')
            return
        confirmed = self._confirm(
            self._t('playlist_delete_tracks_confirm_title', 'Confirm'),
            self._t('playlist_delete_tracks_confirm_message', 'Are you sure you want to remove {count} tracks from this playlist?', count=len(selected_paths)),
        )
        if not confirmed:
            return
        remover = getattr(self.playlist_controller, 'remove_many_from_playlist', None)
        if not callable(remover):
            self._set_feedback(self._t('track_not_in_playlist', 'Track is not in playlist.'), 'orange')
            return
        try:
            removed = int(remover(playlist_id, selected_paths) or 0)
        except PLAYLIST_VIEW_EXCEPTIONS:
            logger.debug('Unable to remove tracks from playlist.', exc_info=True)
            self._set_feedback(self._t('playlist_delete_error', '❗ Error deleting playlist: {error}', error='remove failed'), 'orange')
            return
        self.reload_playlists()
        message = 'Track removed from playlist.' if removed == 1 else f'{removed} tracks removed from playlist.'
        self._set_feedback(message, 'green' if removed else 'orange')

    def _prompt_playlist_name(self) -> str:
        dialog_cls = getattr(self._wx, 'TextEntryDialog', None)
        if dialog_cls is None:
            self._set_feedback(self._t('playlist_creation_error', 'Error creating playlist: {error}', error='dialog unavailable'), 'orange')
            return ''
        dialog = dialog_cls(
            self.panel,
            message=self._t('playlist_create_prompt', 'Enter new playlist name:'),
            caption=self._t('playlist_create_title', 'Create playlist'),
            value='',
        )
        try:
            if dialog.ShowModal() in _DIALOG_CANCELLED:
                return ''
            getter = getattr(dialog, 'GetValue', None)
            if not callable(getter):
                getter = getattr(dialog, 'GetTextValue', None)
            value = getter() if callable(getter) else ''
            return str(value or '').strip()
        finally:
            destroy = getattr(dialog, 'Destroy', None)
            if callable(destroy):
                destroy()

    def _select_file_paths(self) -> list[str]:
        file_dialog_cls = getattr(self._wx, 'FileDialog', None)
        if file_dialog_cls is None:
            self._set_feedback(self._t('playlist_add_not_available', 'Add to playlist not available.'), 'orange')
            return []
        dialog = file_dialog_cls(self.panel, message=self._t('playlist_add_files_title', 'Select tracks to add'))
        try:
            if dialog.ShowModal() in _DIALOG_CANCELLED:
                return []
            getter = getattr(dialog, 'GetPaths', None)
            if callable(getter):
                return [str(path) for path in getter() if path]
            single_getter = getattr(dialog, 'GetPath', None)
            if callable(single_getter):
                value = single_getter()
                return [str(value)] if value else []
            return []
        finally:
            destroy = getattr(dialog, 'Destroy', None)
            if callable(destroy):
                destroy()

    def _select_folder_path(self) -> str:
        dir_dialog_cls = getattr(self._wx, 'DirDialog', None)
        if dir_dialog_cls is None:
            self._set_feedback(self._t('playlist_add_not_available', 'Add to playlist not available.'), 'orange')
            return ''
        dialog = dir_dialog_cls(self.panel, message=self._t('playlist_add_folder_title', 'Select a folder with media files'))
        try:
            if dialog.ShowModal() in _DIALOG_CANCELLED:
                return ''
            getter = getattr(dialog, 'GetPath', None)
            if callable(getter):
                value = getter()
                return str(value or '').strip()
            getters = getattr(dialog, 'GetPaths', None)
            if callable(getters):
                values = [str(path).strip() for path in getters() if str(path).strip()]
                return values[0] if values else ''
            return ''
        finally:
            destroy = getattr(dialog, 'Destroy', None)
            if callable(destroy):
                destroy()

    def _collect_media_file_paths(self, folder_path: str) -> list[str]:
        folder = Path(str(folder_path or '').strip())
        if not folder.is_dir():
            return []
        media_paths: list[str] = []
        for candidate in sorted(folder.rglob('*'), key=lambda item: str(item).lower()):
            if not candidate.is_file():
                continue
            resolved = str(candidate)
            if is_audio_file(resolved) or is_video_file(resolved):
                media_paths.append(resolved)
        return media_paths

    def _confirm(self, title: str, message: str) -> bool:
        style = getattr(self._wx, 'YES_NO', 0) | getattr(self._wx, 'ICON_QUESTION', 0)
        try:
            result = self._wx.MessageBox(message, title, style)
        except PLAYLIST_VIEW_EXCEPTIONS:
            logger.debug('Unable to show wx confirmation dialog.', exc_info=True)
            return False
        return result == getattr(self._wx, 'YES', result)

    def _update_status_label(self) -> None:
        playlist_count = len(self._playlists)
        track_count = len(self._current_playlist_tracks)
        set_label_text(
            self.status_label,
            self._t('playlist_status', 'Playlists: {playlists} | Visible tracks: {tracks}', playlists=playlist_count, tracks=track_count),
        )


    def _on_playlist_updated(self, payload: Any) -> None:
        playlist_id = None
        if isinstance(payload, dict) and payload.get('id') is not None:
            try:
                playlist_id = int(payload.get('id'))
            except PLAYLIST_VIEW_EXCEPTIONS:
                playlist_id = None
        if isinstance(payload, dict) and payload.get('deleted_id') == self._current_playlist_id:
            self._current_playlist_id = None
        if playlist_id is not None:
            self._current_playlist_id = playlist_id
        self.reload_playlists()

    def _on_player_state_changed(self, payload: Any) -> None:
        self._apply_player_highlight(payload)

    def _apply_player_highlight(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        current_path = ''
        if payload.get('current_track_path'):
            current_path = str(payload.get('current_track_path'))
        elif isinstance(payload.get('current_track'), dict) and payload['current_track'].get('path'):
            current_path = str(payload['current_track'].get('path'))
        elif getattr(payload.get('current_track'), 'path', None):
            current_path = str(payload['current_track'].path)
        self._current_track_path = current_path
        self._refresh_now_playing_highlight()

    def shutdown(self) -> None:
        """Persist live playlist widths and release PlaylistView subscriptions.

        Edge cases handled deterministically:
        1. Column drag completion may never fire on some platforms, so shutdown persistence avoids silent data loss.
        2. Playlist and track tables can be resized independently, therefore both are sampled every time.
        3. Shutdown may run more than once, so persistence stays idempotent and best-effort.
        """
        self._persist_playlist_column_widths()
        self._persist_track_column_widths()
        for event_type, subscription in tuple(self._subscriptions):
            unsubscribe = getattr(self.event_bus, 'unsubscribe', None)
            if callable(unsubscribe):
                try:
                    unsubscribe(event_type, subscription=subscription)
                except PLAYLIST_VIEW_EXCEPTIONS:
                    logger.debug('Unable to unsubscribe wx PlaylistView.', exc_info=True)
        self._subscriptions.clear()
        unregister_callback(
            self.localization_manager,
            'unregister_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to unregister wx PlaylistView language callback.',
        )
        unregister_callback(
            self.theme_manager,
            'unregister_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to unregister wx PlaylistView theme callback.',
        )

    def show(self) -> None:
        self.panel.Show(True)
