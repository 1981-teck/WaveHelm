from __future__ import annotations

import importlib
import logging
from typing import Any

from src.audio.audio_event_models import AudioEventType
from src.model.media_file import MediaFile, MediaType
from src.ui_wx.collection_view_support import (
    canonical_media_path,
    filter_library_media,
    library_row_values,
    select_file_paths,
    select_folder_path,
    selected_items,
    sort_library_media,
)
from src.ui_wx.common import (
    WX_CALLBACK_EXCEPTIONS,
    apply_colors,
    autosize_choice_control,
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

LIBRARY_VIEW_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
_DIALOG_CANCELLED = {0, -1}


class LibraryView:
    """wx-based library browser and import view.

    Edge cases handled deterministically:
    1. Missing dialogs or unsupported wx controls fall back to explicit feedback instead of a half-working import flow.
    2. Selection can become stale after filtering, sorting, or external library updates, so playback/removal resolves paths from the current rendered snapshot.
    3. Partial controller capabilities degrade to bounded no-op feedback rather than leaving the page in an inconsistent state.
    """

    FEEDBACK_EVENT_TYPE = AudioEventType.FEEDBACK_MESSAGE
    _set_feedback = set_view_feedback

    COLUMN_WIDTHS_SETTING_KEY = 'ui_library_column_widths'

    COLUMN_KEYS: tuple[tuple[str, str], ...] = (
        ('title', 'library_column_title'),
        ('artist', 'library_column_artist'),
        ('album', 'library_column_album'),
        ('duration', 'library_column_duration'),
        ('type', 'library_column_type'),
        ('path', 'library_column_path'),
    )

    FILTER_OPTIONS: tuple[tuple[str, MediaType, str], ...] = (
        ('all', MediaType.ALL, 'library_filter_all'),
        ('audio', MediaType.AUDIO, 'library_filter_audio'),
        ('video', MediaType.VIDEO, 'library_filter_video'),
    )

    SORT_OPTIONS: tuple[tuple[str, str], ...] = (
        ('title', 'library_sort_title'),
        ('artist', 'library_sort_artist'),
        ('album', 'library_sort_album'),
        ('duration', 'library_sort_duration'),
    )

    def __init__(
        self,
        parent: Any,
        library_controller: Any = None,
        player_controller: Any = None,
        localization_manager: Any = None,
        event_bus: Any = None,
        theme_manager: Any = None,
        settings_manager: Any = None,
        **_: Any,
    ) -> None:
        self.parent = parent
        self.library_controller = library_controller
        self.player_controller = player_controller
        self.localization_manager = localization_manager
        self.event_bus = event_bus
        self.theme_manager = theme_manager
        self.settings_manager = settings_manager
        self._wx = self._import_wx_module()
        self.panel = self._wx.Panel(parent)
        self.panel.owner = self
        self._subscriptions: list[tuple[AudioEventType, Any]] = []
        self._displayed_media: list[MediaFile] = []
        self._current_track_path: str = ''
        self._filter_values: list[str] = []
        self._sort_values: list[str] = []
        self._build_ui()
        self._bind_events()
        self._register_callbacks()
        self._subscribe_to_events()
        self.update_localization()
        self.update_theme_colors()
        self.refresh_library()

    @staticmethod
    def _import_wx_module() -> Any:
        return importlib.import_module('wx')

    def _build_ui(self) -> None:
        wx = self._wx
        root = wx.BoxSizer(wx.VERTICAL)
        self.title_label = wx.StaticText(self.panel, label='')
        root.Add(self.title_label, 0, wx.ALL | wx.EXPAND, 8)
        root.Add(self._build_search_row(), 0, wx.ALL | wx.EXPAND, 8)
        root.Add(self._build_actions_row(), 0, wx.ALL | wx.EXPAND, 8)
        self.table = self._build_table()
        root.Add(self.table, 1, wx.ALL | wx.EXPAND, 8)
        self.status_label = wx.StaticText(self.panel, label='')
        self.feedback_label = wx.StaticText(self.panel, label='')
        root.Add(self.status_label, 0, wx.ALL | wx.EXPAND, 8)
        root.Add(self.feedback_label, 0, wx.ALL | wx.EXPAND, 8)
        self.panel.SetSizer(root)

    def _build_search_row(self) -> Any:
        wx = self._wx
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.search_label = wx.StaticText(self.panel, label='')
        self.search_text = wx.TextCtrl(self.panel, value='')
        self.filter_label = wx.StaticText(self.panel, label='')
        self.filter_choice = wx.Choice(self.panel)
        self.sort_label = wx.StaticText(self.panel, label='')
        self.sort_choice = wx.Choice(self.panel)
        for widget, proportion in (
            (self.search_label, 0),
            (self.search_text, 1),
            (self.filter_label, 0),
            (self.filter_choice, 0),
            (self.sort_label, 0),
            (self.sort_choice, 0),
        ):
            flags = wx.ALL | (wx.EXPAND if proportion else wx.ALIGN_CENTER_VERTICAL)
            row.Add(widget, proportion, flags, 6)
        return row

    def _build_actions_row(self) -> Any:
        wx = self._wx
        row = create_flow_sizer(wx)
        self.add_files_button = wx.Button(self.panel, label='')
        self.add_folder_button = wx.Button(self.panel, label='')
        self.refresh_button = wx.Button(self.panel, label='')
        self.play_button = wx.Button(self.panel, label='')
        self.favorite_button = wx.Button(self.panel, label='')
        self.remove_button = wx.Button(self.panel, label='')
        for button in (
            self.add_files_button,
            self.add_folder_button,
            self.refresh_button,
            self.play_button,
            self.favorite_button,
            self.remove_button,
        ):
            row.Add(button, 0, wx.ALL | wx.EXPAND, 6)
        return row

    def _build_table(self) -> Any:
        list_ctrl = self._wx.ListCtrl(self.panel, style=getattr(self._wx, 'LC_REPORT', 0))
        for index, (name, key) in enumerate(self.COLUMN_KEYS):
            list_ctrl.InsertColumn(index, self._t(key, name.title()))
        return list_ctrl

    def _bind_events(self) -> None:
        self.search_text.Bind(getattr(self._wx, 'EVT_TEXT', ''), self._on_search_changed)
        self.filter_choice.Bind(self._wx.EVT_CHOICE, self._on_filter_changed)
        self.sort_choice.Bind(self._wx.EVT_CHOICE, self._on_sort_changed)
        self.add_files_button.Bind(self._wx.EVT_BUTTON, self._on_add_files)
        self.add_folder_button.Bind(self._wx.EVT_BUTTON, self._on_add_folder)
        self.refresh_button.Bind(self._wx.EVT_BUTTON, self._on_refresh)
        self.play_button.Bind(self._wx.EVT_BUTTON, self._on_play_selected)
        self.favorite_button.Bind(self._wx.EVT_BUTTON, self._on_add_to_favorites)
        self.remove_button.Bind(self._wx.EVT_BUTTON, self._on_remove_selected)
        self.table.Bind(getattr(self._wx, 'EVT_LIST_ITEM_ACTIVATED', ''), self._on_item_activated)
        column_resize_event = getattr(self._wx, 'EVT_LIST_COL_END_DRAG', None)
        if column_resize_event is not None:
            self.table.Bind(column_resize_event, self._on_table_column_resized)

    def _register_callbacks(self) -> None:
        register_callback(
            self.localization_manager,
            'register_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to register wx LibraryView language callback.',
        )
        register_callback(
            self.theme_manager,
            'register_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to register wx LibraryView theme callback.',
        )

    def _subscribe_to_events(self) -> None:
        self._subscribe(AudioEventType.LIBRARY_UPDATED, self._on_library_updated)
        self._subscribe(AudioEventType.MEDIA_DURATION_UPDATE, self._on_media_duration_update)
        self._subscribe(AudioEventType.PLAYER_STATE_CHANGED, self._on_player_state_changed)

    def _subscribe(self, event_type: AudioEventType, callback: Any) -> None:
        subscribe_event(
            self.event_bus,
            self._subscriptions,
            event_type,
            callback,
            exceptions=LIBRARY_VIEW_EXCEPTIONS,
            logger=logger,
            view_name='LibraryView',
        )

    def _t(self, key: str, default: str | None = None, **kwargs: Any) -> str:
        fallback = default if default is not None else key
        return get_localized_text(self.localization_manager, key, fallback, **kwargs)

    def _selected_filter(self) -> MediaType:
        index = self.filter_choice.GetSelection()
        if 0 <= index < len(self.FILTER_OPTIONS):
            return self.FILTER_OPTIONS[index][1]
        return MediaType.ALL

    def _selected_sort(self) -> str:
        index = self.sort_choice.GetSelection()
        if 0 <= index < len(self.SORT_OPTIONS):
            return self.SORT_OPTIONS[index][0]
        return 'title'

    def update_localization(self, *_: Any) -> None:
        set_label_text(self.title_label, self._t('nav_library', 'Library'))
        set_label_text(self.search_label, self._t('library_search_label', 'Search'))
        set_label_text(self.filter_label, self._t('library_filter_label', 'Filter'))
        set_label_text(self.sort_label, self._t('library_sort_label', 'Sort'))
        self._set_choice_items()
        self._set_button_labels()
        self._set_column_labels()
        self._restore_column_widths()
        self._update_status_label()

    def _set_choice_items(self) -> None:
        current_filter = self._selected_filter()
        current_sort = self._selected_sort()
        self._filter_values = [name for name, _kind, _key in self.FILTER_OPTIONS]
        filter_labels = [self._t(key, name.title()) for name, _kind, key in self.FILTER_OPTIONS]
        self.filter_choice.SetItems(filter_labels)
        autosize_choice_control(self.filter_choice, filter_labels)
        filter_index = next((idx for idx, (_name, kind, _key) in enumerate(self.FILTER_OPTIONS) if kind == current_filter), 0)
        self.filter_choice.SetSelection(filter_index)
        self._sort_values = [name for name, _key in self.SORT_OPTIONS]
        sort_labels = [self._t(key, name.title()) for name, key in self.SORT_OPTIONS]
        self.sort_choice.SetItems(sort_labels)
        autosize_choice_control(self.sort_choice, sort_labels)
        sort_index = next((idx for idx, (name, _key) in enumerate(self.SORT_OPTIONS) if name == current_sort), 0)
        self.sort_choice.SetSelection(sort_index)

    def _set_button_labels(self) -> None:
        """Apply localized action labels using keys already populated across the shipped locale bundles.

        Edge cases handled deterministically:
        1. Legacy library-specific button keys may be missing from locale files, so we prefer shared action keys with existing translations.
        2. Favorites has no dedicated action key in the locale bundles, so we fall back to the localized module label instead of leaving the button stuck in English.
        3. Localization refresh can happen repeatedly at runtime, so labels are reassigned idempotently without relying on constructor defaults.
        """
        set_label_text(self.add_files_button, self._t('add_files_button', 'Add files'))
        set_label_text(self.add_folder_button, self._t('add_folder_button', 'Add folder'))
        set_label_text(self.refresh_button, self._t('btn_refresh', 'Refresh'))
        set_label_text(self.play_button, self._t('tooltip_play', 'Play selected'))
        set_label_text(self.favorite_button, self._t('nav_favorites', 'Favorites'))
        set_label_text(self.remove_button, self._t('tooltip_remove', 'Remove selected'))

    def _set_column_labels(self) -> None:
        columns = getattr(self.table, 'columns', None)
        for index, (name, key) in enumerate(self.COLUMN_KEYS):
            label = self._t(key, name.title())
            set_listctrl_column_label(self.table, index, label)

    def _restore_column_widths(self) -> None:
        restore_listctrl_column_widths(
            self.table,
            self.settings_manager,
            self.COLUMN_WIDTHS_SETTING_KEY,
            len(self.COLUMN_KEYS),
        )

    def _persist_column_widths(self) -> None:
        persist_listctrl_column_widths(
            self.table,
            self.settings_manager,
            self.COLUMN_WIDTHS_SETTING_KEY,
            len(self.COLUMN_KEYS),
        )

    def _on_table_column_resized(self, _event: Any) -> None:
        self._persist_column_widths()


    def update_theme_colors(self, *_: Any) -> None:
        colors = get_theme_colors(self.theme_manager)
        background = colors.get('panel_bg') or colors.get('bg_color')
        foreground = colors.get('text_color')
        accent = colors.get('button_color') or background
        apply_colors(self.panel, background=background, foreground=foreground)
        for widget in (
            self.title_label,
            self.search_label,
            self.search_text,
            self.filter_label,
            self.filter_choice,
            self.sort_label,
            self.sort_choice,
            self.table,
            self.status_label,
            self.feedback_label,
        ):
            apply_colors(widget, background=background, foreground=foreground)
        for button in (
            self.add_files_button,
            self.add_folder_button,
            self.refresh_button,
            self.play_button,
            self.favorite_button,
            self.remove_button,
        ):
            apply_colors(button, background=accent, foreground=foreground)
        self._refresh_now_playing_highlight(colors)

    def refresh_library(self) -> None:
        filtered = filter_library_media(
            self._all_media(),
            query=self.search_text.GetValue() or '',
            selected_filter=self._selected_filter(),
        )
        self._displayed_media = sort_library_media(filtered, sort_name=self._selected_sort())
        self._populate_rows()
        self._update_status_label()

    def _all_media(self) -> list[MediaFile]:
        getter = getattr(self.library_controller, 'get_all_media', None)
        if not callable(getter):
            return []
        try:
            items = getter()
        except LIBRARY_VIEW_EXCEPTIONS:
            logger.debug('Unable to read library media.', exc_info=True)
            return []
        return [item for item in items if isinstance(item, MediaFile)]

    def _populate_rows(self) -> None:
        self.table.DeleteAllItems()
        for row_index, media in enumerate(self._displayed_media):
            row_values = self._row_values(media)
            inserted = self.table.InsertItem(row_index, row_values[0])
            for column_index, value in enumerate(row_values[1:], start=1):
                self.table.SetItem(inserted, column_index, value)
        self._refresh_now_playing_highlight()

    @staticmethod
    def _canonical_path(path: str) -> str:
        return canonical_media_path(path, exceptions=LIBRARY_VIEW_EXCEPTIONS)


    def _refresh_now_playing_highlight(self, colors: dict[str, str] | None = None) -> None:
        refresh_now_playing_highlight(
            self.table,
            self._displayed_media,
            self._current_track_path,
            self._canonical_path,
            self.theme_manager,
            colors=colors,
            exceptions=LIBRARY_VIEW_EXCEPTIONS,
            logger=logger,
            view_name='LibraryView',
            table_name='table',
        )

    def _row_values(self, media: MediaFile) -> list[str]:
        return library_row_values(
            media,
            media_type_text=self._media_type_text,
            duration_text=lambda seconds: format_duration(seconds, include_hours=True),
        )

    def _media_type_text(self, media_type: MediaType) -> str:
        if media_type is MediaType.AUDIO:
            return self._t('library_type_audio', 'Audio')
        if media_type is MediaType.VIDEO:
            return self._t('library_type_video', 'Video')
        return self._t('library_type_unknown', 'Unknown')


    def get_selected_media_files(self) -> list[MediaFile]:
        return selected_items(self.table, self._displayed_media)

    def _on_search_changed(self, _event: Any | None = None) -> None:
        self.refresh_library()

    def _on_filter_changed(self, _event: Any | None = None) -> None:
        self.refresh_library()

    def _on_sort_changed(self, _event: Any | None = None) -> None:
        self.refresh_library()

    def _on_refresh(self, _event: Any | None = None) -> None:
        self.refresh_library()

    def _on_item_activated(self, _event: Any | None = None) -> None:
        self._on_play_selected()

    def _on_play_selected(self, _event: Any | None = None) -> None:
        """Start playback from the selected library item with bounded UI error handling.

        Edge cases handled deterministically:
        1. Backend playback context setup can raise controller/runtime exceptions, so the wx callback contains them and shows explicit failure feedback.
        2. Some controllers expose a boolean result while others return ``None`` and publish state asynchronously, so success is inferred defensively instead of assuming an unconditional happy path.
        3. Backend state can already be in ``ERROR`` after the request, so the library must not show a false success message in that case.
        """
        media_items = self.get_selected_media_files()
        if not media_items:
            self._set_feedback(self._t('library_select_media_first', 'Select at least one library item first.'), 'orange')
            return
        selected = media_items[0]
        start_index = self._displayed_media.index(selected) if selected in self._displayed_media else 0
        setter = getattr(self.player_controller, 'set_playback_context', None)
        if not callable(setter):
            self._set_feedback(self._t('library_playback_unavailable', 'Playback is not available.'), 'orange')
            return
        try:
            result = setter(items=list(self._displayed_media), start_index=start_index, autoplay=True)
        except LIBRARY_VIEW_EXCEPTIONS + WX_CALLBACK_EXCEPTIONS:
            logger.debug('Unable to start playback from LibraryView.', exc_info=True)
            self._set_feedback(self._t('library_playback_failed', 'Unable to start playback for the selected library item.'), 'orange')
            return
        if result is False or not self._playback_request_matches_selection(selected, start_index):
            self._set_feedback(self._t('library_playback_failed', 'Unable to start playback for the selected library item.'), 'orange')
            return
        self._set_feedback(self._t('library_playback_started', 'Playback started from the selected library item.'), 'green')

    def _playback_request_matches_selection(self, selected: MediaFile, start_index: int) -> bool:
        payload_getter = getattr(self.player_controller, 'get_current_player_state_payload', None)
        if not callable(payload_getter):
            return True
        try:
            payload = payload_getter() or {}
        except LIBRARY_VIEW_EXCEPTIONS + WX_CALLBACK_EXCEPTIONS:
            logger.debug('Unable to read player state payload for LibraryView.', exc_info=True)
            return True
        state_name = str(payload.get('state', '') or '').upper()
        if state_name == 'ERROR':
            return False
        current_index = payload.get('current_index')
        if isinstance(current_index, int) and current_index != start_index:
            return False
        current_track = payload.get('current_track')
        if isinstance(current_track, dict):
            current_path = str(current_track.get('path', '') or '')
            if current_path and current_path != getattr(selected, 'path', ''):
                return False
        return True

    def _on_add_to_favorites(self, _event: Any | None = None) -> None:
        selected_media = self.get_selected_media_files()
        if not selected_media:
            self._set_feedback(self._t('library_select_favorites_first', 'Select at least one item to add to favorites.'), 'orange')
            return
        added = 0
        for media in selected_media:
            if self._add_media_to_favorites(media):
                added += 1
        if added <= 0:
            self._set_feedback(self._t('library_favorites_skipped', 'Selected items were already in favorites or invalid.'), 'orange')
            return
        self._set_feedback(self._t('library_favorites_added', 'Added {count} item(s) to favorites.', count=added), 'green')

    def _add_media_to_favorites(self, media: MediaFile) -> bool:
        adder = getattr(self.library_controller, 'add_to_favorites', None)
        if not callable(adder):
            return False
        try:
            return bool(adder(media, emit_feedback=False, emit_event=True))
        except LIBRARY_VIEW_EXCEPTIONS:
            logger.debug('Unable to add media to favorites.', exc_info=True)
            return False

    def _on_remove_selected(self, _event: Any | None = None) -> None:
        selected_media = self.get_selected_media_files()
        if not selected_media:
            self._set_feedback(self._t('library_select_remove_first', 'Select at least one item to remove.'), 'orange')
            return
        remover = getattr(self.library_controller, 'remove_media_by_path', None)
        if not callable(remover):
            self._set_feedback(self._t('library_remove_unavailable', 'Removal is not available.'), 'orange')
            return
        removed = 0
        for media in selected_media:
            try:
                if remover(getattr(media, 'path', '')):
                    removed += 1
            except LIBRARY_VIEW_EXCEPTIONS:
                logger.debug('Unable to remove media from library.', exc_info=True)
        self.refresh_library()
        self._set_feedback(self._t('library_removed_feedback', 'Removed {count} item(s) from the library.', count=removed), 'green' if removed else 'orange')

    def _on_add_files(self, _event: Any | None = None) -> None:
        paths = self._select_file_paths()
        if not paths:
            return
        self._import_paths(paths)

    def _on_add_folder(self, _event: Any | None = None) -> None:
        folder = self._select_folder_path()
        if not folder:
            return
        self._import_paths([folder])

    def _select_file_paths(self) -> list[str]:
        return select_file_paths(
            self._wx,
            self.panel,
            message=self._t('library_add_files_button', 'Add files'),
            cancelled_results=_DIALOG_CANCELLED,
            unavailable=lambda: self._set_feedback(
                self._t('library_dialog_unavailable', 'Import dialog is not available on this runtime.'),
                'orange',
            ),
        )

    def _select_folder_path(self) -> str:
        return select_folder_path(
            self._wx,
            self.panel,
            message=self._t('library_add_folder_button', 'Add folder'),
            cancelled_results=_DIALOG_CANCELLED,
            unavailable=lambda: self._set_feedback(
                self._t('library_dialog_unavailable', 'Import dialog is not available on this runtime.'),
                'orange',
            ),
            allow_paths_fallback=False,
        )

    def _import_paths(self, paths: list[str]) -> None:
        adder = getattr(self.library_controller, 'add_media_files', None)
        if not callable(adder):
            self._set_feedback(self._t('library_import_unavailable', 'Library import is not available.'), 'orange')
            return
        try:
            adder(paths, emit_event=True, emit_feedback=True)
        except LIBRARY_VIEW_EXCEPTIONS:
            logger.debug('Unable to import paths into the library.', exc_info=True)
            self._set_feedback(self._t('library_import_failed', 'Unable to import the selected media.'), 'orange')
            return
        self.refresh_library()
        self._set_feedback(self._t('library_import_done', 'Import completed.'), 'green')

    def _update_status_label(self) -> None:
        count = len(self._displayed_media)
        total = len(self._all_media())
        set_label_text(self.status_label, self._t('library_status', 'Visible items: {count} / {total}', count=count, total=total))


    def _on_library_updated(self, _payload: Any) -> None:
        self.refresh_library()

    def _on_media_duration_update(self, payload: Any) -> None:
        path = str((payload or {}).get('path', '') or '') if isinstance(payload, dict) else ''
        duration = float((payload or {}).get('duration', 0.0) or 0.0) if isinstance(payload, dict) else 0.0
        if not path:
            self.refresh_library()
            return
        for index, media in enumerate(self._displayed_media):
            if getattr(media, 'path', '') != path:
                continue
            media.duration = duration
            self.table.SetItem(index, 3, format_duration(duration, include_hours=True))
            return
        self.refresh_library()

    def _on_player_state_changed(self, payload: Any) -> None:
        current_track = (payload or {}).get('current_track', {}) if isinstance(payload, dict) else {}
        title = str(current_track.get('title', '') or '') if isinstance(current_track, dict) else ''
        current_path = ''
        if isinstance(payload, dict):
            raw_path = payload.get('current_track_path') or payload.get('path')
            if raw_path:
                current_path = str(raw_path)
            elif not payload.get('stopped') and isinstance(current_track, dict) and current_track.get('path'):
                current_path = str(current_track.get('path'))
        self._current_track_path = current_path
        self._refresh_now_playing_highlight()
        if title:
            self._set_feedback(self._t('library_now_playing_feedback', 'Now playing: {title}', title=title), 'green')

    def shutdown(self) -> None:
        """Persist live column widths and release LibraryView subscriptions.

        Edge cases handled deterministically:
        1. Some wx ports do not emit the column-drag completion event, so shutdown persistence is the final safety net.
        2. Runtime localization can rebuild labels before teardown, therefore widths are sampled from the live ListCtrl.
        3. Persistence failures must not block shutdown, so the shared helper contains toolkit-specific exceptions.
        """
        self._persist_column_widths()
        for event_type, subscription in tuple(self._subscriptions):
            unsubscribe = getattr(self.event_bus, 'unsubscribe', None)
            if callable(unsubscribe):
                try:
                    unsubscribe(event_type, subscription=subscription)
                except LIBRARY_VIEW_EXCEPTIONS:
                    logger.debug('Unable to unsubscribe wx LibraryView.', exc_info=True)
        self._subscriptions.clear()
        unregister_callback(
            self.localization_manager,
            'unregister_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to unregister wx LibraryView language callback.',
        )
        unregister_callback(
            self.theme_manager,
            'unregister_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to unregister wx LibraryView theme callback.',
        )

    def show(self) -> None:
        self.panel.Show(True)
