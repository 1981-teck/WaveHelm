from __future__ import annotations

import logging
import sys

from src.ui_wx.about_view import AboutView
from src.ui_wx.ambient_view import AmbientView
from src.ui_wx.readmi_view import ReadmiView
from tests import test_wx_effects_view as effects_tests
from tests import test_wx_equalizer_view as equalizer_tests
from tests import test_wx_favorites_view as favorites_tests
from tests import test_wx_library_view as library_tests
from tests import test_wx_main_view as main_view_tests
from tests import test_wx_mini_player as mini_player_tests
from tests import test_wx_playlist_view as playlist_tests
from tests import test_wx_settings_view as settings_tests
from tests import test_wx_simple_views as simple_views_tests
from tests import test_wx_video_view as video_tests
from tests import test_wx_visualizer_view as visualizer_tests
from tests.wx_fakes import FakeWxModule


def _raise_registration_error(_self, _callback):
    raise RuntimeError('registration failed')


def _patch_registration_failures(monkeypatch, localization_cls, theme_cls) -> None:
    monkeypatch.setattr(localization_cls, 'register_language_change_callback', _raise_registration_error, raising=False)
    monkeypatch.setattr(theme_cls, 'register_theme_change_callback', _raise_registration_error, raising=False)


def _build_about(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    return AboutView(
        FakeWxModule.Panel(None),
        localization_manager=simple_views_tests.DummyLocalizationManager(),
        theme_manager=simple_views_tests.DummyThemeManager(),
    )


def _build_readmi(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    return ReadmiView(
        FakeWxModule.Panel(None),
        localization_manager=simple_views_tests.DummyLocalizationManager(),
        theme_manager=simple_views_tests.DummyThemeManager(),
    )


def _build_ambient(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    return AmbientView(
        FakeWxModule.Panel(None),
        ambient_manager=simple_views_tests.DummyAmbientManager(),
        event_bus=simple_views_tests.DummyEventBus(),
        localization_manager=simple_views_tests.DummyLocalizationManager(),
        theme_manager=simple_views_tests.DummyThemeManager(),
    )


def test_wx_simple_views_log_callback_registration_failures(monkeypatch, caplog):
    _patch_registration_failures(
        monkeypatch,
        simple_views_tests.DummyLocalizationManager,
        simple_views_tests.DummyThemeManager,
    )

    with caplog.at_level(logging.DEBUG):
        _build_about(monkeypatch)
        _build_readmi(monkeypatch)
        _build_ambient(monkeypatch)

    messages = {record.message for record in caplog.records}
    assert 'Unable to register wx AboutView theme callback.' in messages
    assert 'Unable to register wx AboutView language callback.' in messages
    assert 'Unable to register wx ReadmiView theme callback.' in messages
    assert 'Unable to register wx ReadmiView language callback.' in messages
    assert 'Unable to register wx AmbientView theme callback.' in messages
    assert 'Unable to register wx AmbientView language callback.' in messages


def test_wx_data_views_log_callback_registration_failures(monkeypatch, caplog):
    _patch_registration_failures(monkeypatch, library_tests.DummyLocalizationManager, library_tests.DummyThemeManager)
    _patch_registration_failures(monkeypatch, playlist_tests.DummyLocalizationManager, playlist_tests.DummyThemeManager)
    _patch_registration_failures(monkeypatch, favorites_tests.DummyLocalizationManager, favorites_tests.DummyThemeManager)

    with caplog.at_level(logging.DEBUG):
        library_tests.build_library_view(monkeypatch)
        playlist_tests.build_playlist_view(monkeypatch)
        favorites_tests.build_favorites_view(monkeypatch)

    messages = {record.message for record in caplog.records}
    assert 'Unable to register wx LibraryView theme callback.' in messages
    assert 'Unable to register wx LibraryView language callback.' in messages
    assert 'Unable to register wx PlaylistView theme callback.' in messages
    assert 'Unable to register wx PlaylistView language callback.' in messages
    assert 'Unable to register wx FavoritesView theme callback.' in messages
    assert 'Unable to register wx FavoritesView language callback.' in messages


def test_wx_controls_views_log_callback_registration_failures(monkeypatch, caplog):
    _patch_registration_failures(monkeypatch, equalizer_tests.DummyLocalizationManager, equalizer_tests.DummyThemeManager)
    _patch_registration_failures(monkeypatch, effects_tests.DummyLocalizationManager, effects_tests.DummyThemeManager)
    _patch_registration_failures(monkeypatch, settings_tests.DummyLocalizationManager, settings_tests.DummyThemeManager)
    _patch_registration_failures(monkeypatch, mini_player_tests.DummyLocalizationManager, mini_player_tests.DummyThemeManager)

    with caplog.at_level(logging.DEBUG):
        equalizer_tests.build_equalizer_view(monkeypatch)
        effects_tests.build_effects_view(monkeypatch)
        settings_tests.build_settings_view(monkeypatch)
        mini_player_tests.build_mini_player(monkeypatch)

    messages = {record.message for record in caplog.records}
    assert 'Unable to register wx EqualizerView theme callback.' in messages
    assert 'Unable to register wx EqualizerView language callback.' in messages
    assert 'Unable to register wx EffectsView theme callback.' in messages
    assert 'Unable to register wx EffectsView language callback.' in messages
    assert 'Unable to register wx SettingsView theme callback.' in messages
    assert 'Unable to register wx SettingsView language callback.' in messages
    assert 'Unable to register wx MiniPlayer theme callback.' in messages
    assert 'Unable to register wx MiniPlayer language callback.' in messages


def test_wx_runtime_views_log_callback_registration_failures(monkeypatch, caplog):
    """External-only runtime views should log only active view callback failures.

    Edge cases covered:
    1. VisualizerView still registers runtime callbacks and must keep logging failures.
    2. MainView remains active after the embedded video page removal and must still log its own registration failures.
    3. VideoView is now a compatibility stub, so runtime callback tests must not expect registration messages for a page that is no longer instantiated.
    """
    _patch_registration_failures(monkeypatch, visualizer_tests.DummyLocalizationManager, visualizer_tests.DummyThemeManager)
    _patch_registration_failures(monkeypatch, video_tests.DummyLocalizationManager, video_tests.DummyThemeManager)

    with caplog.at_level(logging.DEBUG):
        visualizer_tests.build_visualizer_view(monkeypatch)
        video_tests.build_main_view(monkeypatch)

    messages = {record.message for record in caplog.records}
    assert 'Unable to register wx VisualizerView theme callback.' in messages
    assert 'Unable to register wx VisualizerView language callback.' in messages
    assert 'Unable to register wx MainView theme callback.' in messages
    assert 'Unable to register wx MainView language callback.' in messages
    assert 'Unable to register wx VideoView theme callback.' not in messages
    assert 'Unable to register wx VideoView language callback.' not in messages
