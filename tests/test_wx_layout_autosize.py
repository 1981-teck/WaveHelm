from __future__ import annotations

import sys

from src.ui_wx.common import autosize_choice_control, set_label_text
from tests.test_wx_effects_view import build_effects_view
from tests.test_wx_equalizer_view import build_equalizer_view
from tests.test_wx_favorites_view import build_favorites_view
from tests.test_wx_library_view import build_library_view
from tests.test_wx_playlist_view import build_playlist_view
from tests.test_wx_settings_view import build_settings_view
from tests.test_wx_simple_views import DummyAmbientManager, DummyEventBus as SimpleEventBus, DummyLocalizationManager as SimpleLocalizationManager, DummyThemeManager as SimpleThemeManager
from tests.test_wx_video_view import build_main_view
from tests.test_wx_visualizer_view import build_visualizer_view
from src.ui_wx.ambient_view import AmbientView
from tests.wx_fakes import FakeCheckBox, FakeChoice, FakeWxModule


def test_set_label_text_autosizes_button_and_checkbox(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    button = FakeWxModule.Button(None, label='')
    checkbox = FakeCheckBox(None, label='')

    set_label_text(button, 'Riproduci selezionati')
    set_label_text(checkbox, 'Usa finestra video esterna')

    assert button.min_size is not None
    assert checkbox.min_size is not None
    assert button.min_size[0] >= 16 + len(button.label) * 8
    assert checkbox.min_size[0] >= 16 + len(checkbox.label) * 8


def test_autosize_choice_control_uses_longest_item(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    choice = FakeChoice(None)
    items = ['Breve', 'Molto molto lungo', 'Medio']
    choice.SetItems(items)

    autosize_choice_control(choice, items)

    assert choice.min_size is not None
    assert choice.min_size[0] >= 24 + len('Molto molto lungo') * 8


def test_wx_views_expose_autosized_localized_controls(monkeypatch):
    playlist_view, *_ = build_playlist_view(monkeypatch)
    favorites_view, *_ = build_favorites_view(monkeypatch)
    library_view, *_ = build_library_view(monkeypatch)
    settings_view, *_ = build_settings_view(monkeypatch)
    visualizer_view, *_ = build_visualizer_view(monkeypatch)
    equalizer_view, *_ = build_equalizer_view(monkeypatch)
    effects_view, *_ = build_effects_view(monkeypatch)
    ambient_view = AmbientView(
        FakeWxModule.Panel(None),
        ambient_manager=DummyAmbientManager(),
        event_bus=SimpleEventBus(),
        localization_manager=SimpleLocalizationManager(),
        theme_manager=SimpleThemeManager(),
    )
    main_view, _ = build_main_view(monkeypatch)
    mini_player = main_view._mini_player

    controls = [
        playlist_view.create_button,
        playlist_view.add_tracks_button,
        favorites_view.select_all_button,
        library_view.add_folder_button,
        library_view.favorite_button,
        settings_view.open_third_party_notices_button,
        settings_view.open_logs_button,
        visualizer_view.toggle_button,
        equalizer_view.toggle_button,
        effects_view.reset_all_button,
        ambient_view.open_folder_button,
        main_view._sidebar_buttons['visualizer'],
        mini_player.shuffle_button,
    ]
    for control in controls:
        assert control.min_size is not None
        assert control.min_size[0] >= 48

    choices = [
        library_view.filter_choice,
        library_view.sort_choice,
        settings_view.language_choice,
        settings_view.theme_choice,
        visualizer_view.band_choice,
        equalizer_view.preset_choice,
        ambient_view.sound_choice,
    ]
    for choice in choices:
        assert choice.min_size is not None
        assert choice.min_size[0] >= 80
