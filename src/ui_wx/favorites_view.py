from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from typing import Any

from src.audio.audio_event_models import AudioEventType
from src.model.media_file import MediaFile
from src.ui_wx.common import (
    WX_CALLBACK_EXCEPTIONS,
    create_flow_sizer,
    format_duration,
    get_selected_indices,
    refresh_now_playing_highlight,
    set_view_feedback,
    subscribe_event,
    get_localized_text,
    register_callback,
    set_label_text,
)
from src.ui_wx.view_presentation_support import (
    apply_collection_view_theme,
    persist_column_width_groups,
    release_view_lifecycle,
    restore_column_width_groups,
    set_translated_column_labels,
)

logger = logging.getLogger(__name__)

FAVORITES_VIEW_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)


class FavoritesView:
    """wx-based favorites management page.

    Edge cases handled deterministically:
    1. Favorite payloads can be malformed or partially missing, so list refresh skips bad rows instead of breaking the whole page.
    2. Selected paths can be duplicated or case-variant across storage and UI rows, so removals are canonicalized before touching the database.
    3. Player-state events can reference media no longer visible in the table, so highlight refresh is always best-effort and bounded.
    """

    FEEDBACK_EVENT_TYPE = AudioEventType.FEEDBACK_MESSAGE
    _set_feedback = set_view_feedback

    COLUMN_WIDTHS_SETTING_KEY = 'ui_favorites_column_widths'

    COLUMN_KEYS: tuple[tuple[str, str], ...] = (
        ('title', 'favorites_col_title'),
        ('artist', 'favorites_col_artist'),
        ('duration', 'favorites_col_duration'),
        ('path', 'favorites_col_path'),
    )

    def __init__(
        self,
        parent: Any,
        database_manager: Any = None,
        player_controller: Any = None,
        localization_manager: Any = None,
        event_bus: Any = None,
        theme_manager: Any = None,
        settings_manager: Any = None,
        **_: Any,
    ) -> None:
        self._wx = self._import_wx_module()
        self.database_manager = database_manager
        self.player_controller = player_controller
        self.localization_manager = localization_manager
        self.event_bus = event_bus
        self.theme_manager = theme_manager
        self.settings_manager = settings_manager
        self.panel = self._wx.Panel(parent)
        self.panel.owner = self
        self._subscriptions: list[tuple[AudioEventType, Any]] = []
        self._favorites: list[MediaFile] = []
        self._path_map: dict[str, MediaFile] = {}
        self._current_track_path = ''
        self._build_ui()
        self._bind_events()
        self._register_callbacks()
        self._subscribe_to_events()
        self.reload_favorites()
        self.update_localization()
        self.update_theme_colors()

    @staticmethod
    def _import_wx_module() -> Any:
        try:
            return importlib.import_module('wx')
        except ImportError as exc:
            raise RuntimeError('wx is required to instantiate the wx FavoritesView.') from exc

    @staticmethod
    def _canon_path(path: str) -> str:
        try:
            if path and not path.startswith(('http://', 'https://', 'rtsp://', 'rtmp://')):
                return os.path.normcase(os.path.abspath(os.path.normpath(path)))
        except FAVORITES_VIEW_EXCEPTIONS:
            logger.debug('Unable to canonicalize favorite path.', exc_info=True)
        return str(path or '')

    def _build_ui(self) -> None:
        wx = self._wx
        root = wx.BoxSizer(wx.VERTICAL)
        self.title_label = wx.StaticText(self.panel, label='')
        root.Add(self.title_label, 0, wx.ALL | wx.EXPAND, 10)
        root.Add(self._build_toolbar(), 0, wx.ALL | wx.EXPAND, 0)
        self.table = self._build_table()
        root.Add(self.table, 1, wx.ALL | wx.EXPAND, 8)
        self.status_label = wx.StaticText(self.panel, label='')
        self.feedback_label = wx.StaticText(self.panel, label='')
        root.Add(self.status_label, 0, wx.ALL | wx.EXPAND, 8)
        root.Add(self.feedback_label, 0, wx.ALL | wx.EXPAND, 8)
        self.panel.SetSizer(root)

    def _build_toolbar(self) -> Any:
        wx = self._wx
        row = create_flow_sizer(wx)
        self.play_button = wx.Button(self.panel, label='')
        self.remove_button = wx.Button(self.panel, label='')
        self.refresh_button = wx.Button(self.panel, label='')
        self.select_all_button = wx.Button(self.panel, label='')
        for button in (self.play_button, self.remove_button, self.refresh_button, self.select_all_button):
            row.Add(button, 0, wx.ALL | wx.EXPAND, 6)
        return row

    def _build_table(self) -> Any:
        table = self._wx.ListCtrl(self.panel, style=getattr(self._wx, 'LC_REPORT', 0))
        for index, (name, key) in enumerate(self.COLUMN_KEYS):
            table.InsertColumn(index, self._t(key, name.title()))
        return table

    def _bind_events(self) -> None:
        wx = self._wx
        self.play_button.Bind(wx.EVT_BUTTON, self._on_play_selected)
        self.remove_button.Bind(wx.EVT_BUTTON, self._on_remove_selected)
        self.refresh_button.Bind(wx.EVT_BUTTON, self._on_refresh)
        self.select_all_button.Bind(wx.EVT_BUTTON, self._on_select_all)
        self.table.Bind(getattr(wx, 'EVT_LIST_ITEM_ACTIVATED', ''), self._on_item_activated)
        column_resize_event = getattr(wx, 'EVT_LIST_COL_END_DRAG', None)
        if column_resize_event is not None:
            self.table.Bind(column_resize_event, self._on_table_column_resized)

    def _register_callbacks(self) -> None:
        register_callback(
            self.localization_manager,
            'register_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to register wx FavoritesView language callback.',
        )
        register_callback(
            self.theme_manager,
            'register_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to register wx FavoritesView theme callback.',
        )

    def _subscribe_to_events(self) -> None:
        self._subscribe(AudioEventType.FAVORITE_CHANGED, self._on_favorite_changed)
        self._subscribe(AudioEventType.PLAYER_STATE_CHANGED, self._on_player_state_changed)

    def _subscribe(self, event_type: AudioEventType, callback: Any) -> None:
        subscribe_event(
            self.event_bus,
            self._subscriptions,
            event_type,
            callback,
            exceptions=FAVORITES_VIEW_EXCEPTIONS,
            logger=logger,
            view_name='FavoritesView',
        )

    def _t(self, key: str, default: str | None = None, **kwargs: Any) -> str:
        fallback = default if default is not None else key
        return get_localized_text(self.localization_manager, key, fallback, **kwargs)

    def update_localization(self, *_: Any) -> None:
        set_label_text(self.title_label, self._t('nav_favorites', self._t('favorites_header', 'Favorites')))
        set_label_text(self.play_button, self._t('library_play_selected_button', 'Play selected'))
        set_label_text(self.remove_button, self._t('favorites_remove_button', 'Remove selected'))
        set_label_text(self.refresh_button, self._t('library_refresh_button', 'Refresh'))
        set_label_text(self.select_all_button, self._t('favorites_select_all_button', 'Select all'))
        self._set_column_labels()
        self._restore_column_widths()
        self._update_status_label()

    def _set_column_labels(self) -> None:
        set_translated_column_labels(self.table, self.COLUMN_KEYS, self._t)

    def _column_width_groups(self) -> tuple[tuple[object, str, int], ...]:
        return ((self.table, self.COLUMN_WIDTHS_SETTING_KEY, len(self.COLUMN_KEYS)),)

    def _restore_column_widths(self) -> None:
        restore_column_width_groups(self.settings_manager, self._column_width_groups())

    def _persist_column_widths(self) -> None:
        persist_column_width_groups(self.settings_manager, self._column_width_groups())

    def _on_table_column_resized(self, _event: Any) -> None:
        self._persist_column_widths()


    def update_theme_colors(self, *_: Any) -> None:
        colors = apply_collection_view_theme(
            self.theme_manager,
            widgets=(self.panel, self.title_label, self.table, self.status_label, self.feedback_label),
            buttons=(self.play_button, self.remove_button, self.refresh_button, self.select_all_button),
        )
        self._refresh_now_playing_highlight(colors)

    def reload_favorites(self) -> None:
        self._favorites = self._load_media_items()
        self._path_map = self._build_path_map(self._favorites)
        self._populate_rows()
        self._update_status_label()
        self._refresh_now_playing_highlight()

    def _load_media_items(self) -> list[MediaFile]:
        getter = getattr(self.database_manager, 'get_all_favorite_items', None)
        if not callable(getter):
            return []
        try:
            items = getter()
        except FAVORITES_VIEW_EXCEPTIONS:
            logger.debug('Unable to read favorite items.', exc_info=True)
            return []
        return self._convert_media_items(items)

    def _convert_media_items(self, items: Any) -> list[MediaFile]:
        converted: list[MediaFile] = []
        for item in list(items or []):
            media = item if isinstance(item, MediaFile) else MediaFile.from_dict(item) if isinstance(item, dict) else None
            if media is not None:
                converted.append(media)
        return converted

    def _build_path_map(self, items: list[MediaFile]) -> dict[str, MediaFile]:
        mapping: dict[str, MediaFile] = {}
        for media in items:
            path = getattr(media, 'path', '') or ''
            if not path:
                continue
            mapping[path] = media
            mapping[self._canon_path(path)] = media
        return mapping

    def _populate_rows(self) -> None:
        self.table.DeleteAllItems()
        for row_index, media in enumerate(self._favorites):
            values = self._row_values(media)
            inserted = self.table.InsertItem(row_index, values[0])
            for column_index, value in enumerate(values[1:], start=1):
                self.table.SetItem(inserted, column_index, value)


    def _refresh_now_playing_highlight(self, colors: dict[str, str] | None = None) -> None:
        refresh_now_playing_highlight(
            self.table,
            self._favorites,
            self._current_track_path,
            self._canon_path,
            self.theme_manager,
            colors=colors,
            exceptions=FAVORITES_VIEW_EXCEPTIONS,
            logger=logger,
            view_name='FavoritesView',
            table_name='table',
        )

    def _row_values(self, media: MediaFile) -> list[str]:
        metadata = dict(getattr(media, 'metadata', {}) or {})
        artist = str(getattr(media, 'artist', None) or metadata.get('artist', '') or '')
        duration = format_duration(float(getattr(media, 'duration', 0.0) or 0.0))
        path = getattr(media, 'path', '') or ''
        title = getattr(media, 'title', '') or Path(path).stem or self._t('unknown_title', 'Unknown Title')
        return [title, artist, duration, path]



    def _selected_media(self) -> list[MediaFile]:
        selected: list[MediaFile] = []
        for index in get_selected_indices(self.table):
            if 0 <= index < len(self._favorites):
                selected.append(self._favorites[index])
        return selected

    def _selected_paths(self) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for media in self._selected_media():
            path = getattr(media, 'path', '') or ''
            canon = self._canon_path(path)
            if not path or canon in seen:
                continue
            seen.add(canon)
            unique.append(path)
        return unique

    def _on_play_selected(self, _event: Any | None = None) -> None:
        selected = self._selected_media()
        if not selected:
            self._set_feedback(self._t('favorites_select_warning', 'Select a favorite item to play.'), 'orange')
            return
        setter = getattr(self.player_controller, 'set_playback_context', None)
        if not callable(setter):
            self._set_feedback(self._t('library_playback_unavailable', 'Playback is not available.'), 'orange')
            return
        start_index = self._favorites.index(selected[0]) if selected[0] in self._favorites else 0
        setter(items=list(self._favorites), start_index=start_index, autoplay=True)
        self._set_feedback(self._t('favorites_playback_started', '▶ Starting playback from favorites.'), 'green')

    def _on_remove_selected(self, _event: Any | None = None) -> None:
        selected_paths = self._selected_paths()
        if not selected_paths:
            self._set_feedback(self._t('favorites_remove_warning', 'Select one or more favorite items to remove.'), 'orange')
            return
        if not self._confirm_removal(len(selected_paths)):
            return
        removed_count = self._remove_selected_paths(selected_paths)
        self.reload_favorites()
        self._publish_favorite_changed()
        self._set_feedback(self._t('items_removed_from_favorites', 'Removed {count} item(s) from favorites.', count=removed_count), 'green')

    def _confirm_removal(self, count: int) -> bool:
        style = getattr(self._wx, 'YES_NO', 0) | getattr(self._wx, 'ICON_QUESTION', 0)
        title = self._t('favorites_delete_confirm_title', 'Confirm')
        message = self._t('favorites_delete_confirm_message', 'Remove {count} items from favorites?', count=count)
        try:
            result = self._wx.MessageBox(message, title, style)
        except FAVORITES_VIEW_EXCEPTIONS:
            logger.debug('Unable to show wx favorites confirmation dialog.', exc_info=True)
            return False
        return result == getattr(self._wx, 'YES', result)

    def _remove_selected_paths(self, selected_paths: list[str]) -> int:
        remover = getattr(self.database_manager, 'remove_favorite', None)
        if not callable(remover):
            return 0
        removed_count = 0
        for path in selected_paths:
            try:
                remover(path)
            except FAVORITES_VIEW_EXCEPTIONS:
                logger.debug('Unable to remove favorite %s.', path, exc_info=True)
                continue
            removed_count += 1
        return removed_count

    def _publish_favorite_changed(self) -> None:
        publish = getattr(self.event_bus, 'publish', None)
        if callable(publish):
            publish(AudioEventType.FAVORITE_CHANGED, {})

    def _on_refresh(self, _event: Any | None = None) -> None:
        self.reload_favorites()
        self._set_feedback(self._t('library_refresh_button', 'Refresh'), 'green')

    def _on_select_all(self, _event: Any | None = None) -> None:
        for index in range(len(self._favorites)):
            self.table.Select(index, True)
        self._set_feedback(self._t('favorites_select_all_done', 'All favorites selected.'), 'green')

    def _on_item_activated(self, _event: Any | None = None) -> None:
        self._on_play_selected()

    def _on_favorite_changed(self, _payload: Any) -> None:
        self.reload_favorites()

    def _on_player_state_changed(self, payload: Any) -> None:
        current_path = self._extract_current_path(payload)
        self._current_track_path = current_path
        self._refresh_now_playing_highlight()

    def _extract_current_path(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ''
        if payload.get('current_track_path'):
            return str(payload.get('current_track_path'))
        current_track = payload.get('current_track')
        if isinstance(current_track, dict) and current_track.get('path'):
            return str(current_track.get('path'))
        return str(getattr(current_track, 'path', '') or '')

    def _update_status_label(self) -> None:
        set_label_text(
            self.status_label,
            self._t('favorites_status', 'Favorites: {count}', count=len(self._favorites)),
        )


    def shutdown(self) -> None:
        """Persist live favorites widths and release FavoritesView subscriptions.

        Edge cases handled deterministically:
        1. Some sessions end without the resize completion event, so shutdown persistence prevents silent loss.
        2. The live ListCtrl state is authoritative after manual user changes.
        3. Persistence remains best-effort and must not raise during teardown.
        """
        self._persist_column_widths()
        release_view_lifecycle(
            event_bus=self.event_bus,
            subscriptions=self._subscriptions,
            exceptions=FAVORITES_VIEW_EXCEPTIONS,
            localization_manager=self.localization_manager,
            localization_callback=self.update_localization,
            theme_manager=self.theme_manager,
            theme_callback=self.update_theme_colors,
            logger=logger,
            view_name='FavoritesView',
        )

    def show(self) -> None:
        self.panel.Show(True)
