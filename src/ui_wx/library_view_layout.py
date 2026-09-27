"""Native sizer construction, localization and appearance for the Library view."""
from __future__ import annotations

from typing import Any

from src.model.media_file import MediaFile, MediaType
from src.ui_wx.collection_view_support import library_row_values

from src.ui_wx.common import (
    format_duration, autosize_choice_control, create_flow_sizer, get_localized_text, set_label_text,
)
from src.ui_wx.layout_support import dip_size, _relayout_ancestors
from src.ui_wx.view_presentation_support import (
    apply_collection_view_theme, set_translated_column_labels,
    persist_column_width_groups, restore_column_width_groups,
)


class LibraryViewLayout:
    """Keep controls alive while native sizers follow the available client width."""

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
        """Search owns one line; each labelled selector wraps as an indivisible group.

        A fixed two-tier header avoids a width-dependent destructive rebuild. Actual
        wrapping/rectangles are the toolkit's responsibility, not a character formula.
        """
        wx = self._wx
        column = wx.BoxSizer(wx.VERTICAL)
        search = wx.BoxSizer(wx.HORIZONTAL)
        self.search_label = wx.StaticText(self.panel, label='')
        self.search_text = wx.TextCtrl(self.panel, value='')
        margin = dip_size(self.panel, 6)
        search.Add(self.search_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, margin)
        search.Add(self.search_text, 1, wx.ALL | wx.EXPAND, margin)
        self.filter_label = wx.StaticText(self.panel, label='')
        self.filter_choice = wx.Choice(self.panel)
        self.sort_label = wx.StaticText(self.panel, label='')
        self.sort_choice = wx.Choice(self.panel)
        wrap = getattr(wx, 'WrapSizer', None)
        options = wrap(wx.HORIZONTAL) if callable(wrap) else wx.BoxSizer(wx.VERTICAL)
        for label, control in ((self.filter_label, self.filter_choice),
                               (self.sort_label, self.sort_choice)):
            group = wx.BoxSizer(wx.HORIZONTAL)
            group.Add(label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, margin)
            group.Add(control, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, margin)
            options.Add(group, 0, wx.EXPAND)
        column.Add(search, 0, wx.EXPAND)
        column.Add(options, 0, wx.EXPAND)
        return column


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


    def _t(self, key: str, default: str | None = None, **kwargs: Any) -> str:
        fallback = default if default is not None else key
        return get_localized_text(self.localization_manager, key, fallback, **kwargs)


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

        _relayout_ancestors(self.panel)


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
        set_translated_column_labels(self.table, self.COLUMN_KEYS, self._t)


    def update_theme_colors(self, *_: Any) -> None:
        colors = apply_collection_view_theme(
            self.theme_manager,
            widgets=(
                self.panel,
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
            ),
            buttons=(
                self.add_files_button,
                self.add_folder_button,
                self.refresh_button,
                self.play_button,
                self.favorite_button,
                self.remove_button,
            ),
        )
        self._refresh_now_playing_highlight(colors)


    def _column_width_groups(self) -> tuple[tuple[object, str, int], ...]:
        return ((self.table, self.COLUMN_WIDTHS_SETTING_KEY, len(self.COLUMN_KEYS)),)


    def _restore_column_widths(self) -> None:
        restore_column_width_groups(self.settings_manager, self._column_width_groups())


    def _persist_column_widths(self) -> None:
        persist_column_width_groups(self.settings_manager, self._column_width_groups())


    def _on_table_column_resized(self, _event: Any) -> None:
        self._persist_column_widths()


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
