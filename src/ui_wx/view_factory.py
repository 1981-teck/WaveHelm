from __future__ import annotations

from typing import Any

from src.ui_wx.about_view import AboutView
from src.ui_wx.effects_view import EffectsView
from src.ui_wx.equalizer_view import EqualizerView
from src.ui_wx.favorites_view import FavoritesView
from src.ui_wx.ambient_view import AmbientView
from src.ui_wx.library_view import LibraryView
from src.ui_wx.playlist_view import PlaylistView
from src.ui_wx.readmi_view import ReadmiView
from src.ui_wx.settings_view import SettingsView
from src.ui_wx.visualizer_view import VisualizerView

_VIEW_CLASSES = {
    'about': AboutView,
    'ambient': AmbientView,
    'effects': EffectsView,
    'equalizer': EqualizerView,
    'favorites': FavoritesView,
    'library': LibraryView,
    'playlist': PlaylistView,
    'readmi': ReadmiView,
    'settings': SettingsView,
    'visualizer': VisualizerView,
}


def create_page_widget(main_view: Any, view_name: str) -> Any:
    view_class = _VIEW_CLASSES.get(view_name)
    if view_class is None:
        return None
    return view_class(parent=main_view._content_book, **main_view._dependencies)
