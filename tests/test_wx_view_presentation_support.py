from __future__ import annotations

import logging

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.view_presentation_support import (
    apply_collection_view_theme,
    persist_column_width_groups,
    release_view_lifecycle,
    restore_column_width_groups,
    set_translated_column_labels,
)
from tests.wx_fakes import FakeButton, FakeListCtrl, FakeStaticText


TEST_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)


class DummySettings:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get_setting(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def set_setting(self, key: str, value: object) -> None:
        self.values[key] = value


class DummyThemeManager:
    def get_current_theme_colors(self) -> dict[str, str]:
        return {
            'panel_bg': '#101010',
            'text_color': '#eeeeee',
            'button_color': '#303030',
        }


class DummyEventBus:
    def __init__(self) -> None:
        self.calls: list[tuple[AudioEventType, object]] = []

    def unsubscribe(self, event_type: AudioEventType, *, subscription: object) -> None:
        self.calls.append((event_type, subscription))


class DummyManager:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def unregister_language_change_callback(self, callback: object) -> None:
        self.callbacks.append(callback)

    def unregister_theme_change_callback(self, callback: object) -> None:
        self.callbacks.append(callback)


def test_presentation_support_translates_columns_and_round_trips_widths() -> None:
    table = FakeListCtrl()
    table.InsertColumn(0, 'Old A', width=80)
    table.InsertColumn(1, 'Old B', width=90)
    columns = (('title', 'column_title'), ('artist', 'column_artist'))

    set_translated_column_labels(table, columns, lambda key, default: f'{key}:{default}')
    assert table.columns == ['column_title:Title', 'column_artist:Artist']

    settings = DummySettings()
    groups = ((table, 'ui_widths', 2),)
    persist_column_width_groups(settings, groups)
    assert settings.values['ui_widths'] == [80, 90]

    table.SetColumnWidth(0, 10)
    table.SetColumnWidth(1, 10)
    restore_column_width_groups(settings, groups)
    assert table.column_widths == [80, 90]


def test_presentation_support_applies_shared_theme_palette() -> None:
    label = FakeStaticText()
    button = FakeButton()

    colors = apply_collection_view_theme(
        DummyThemeManager(),
        widgets=(label,),
        buttons=(button,),
    )

    assert colors['panel_bg'] == '#101010'
    assert label.background == '#101010'
    assert label.foreground == '#eeeeee'
    assert button.background == '#303030'
    assert button.foreground == '#eeeeee'


def test_presentation_support_releases_subscriptions_and_callbacks() -> None:
    event_bus = DummyEventBus()
    localization = DummyManager()
    theme = DummyManager()
    subscription = object()
    subscriptions = [(AudioEventType.PLAYER_STATE_CHANGED, subscription)]

    def language_callback() -> None:
        return None

    def theme_callback() -> None:
        return None

    release_view_lifecycle(
        event_bus=event_bus,
        subscriptions=subscriptions,
        exceptions=TEST_EXCEPTIONS,
        localization_manager=localization,
        localization_callback=language_callback,
        theme_manager=theme,
        theme_callback=theme_callback,
        logger=logging.getLogger(__name__),
        view_name='TestView',
    )

    assert subscriptions == []
    assert event_bus.calls == [(AudioEventType.PLAYER_STATE_CHANGED, subscription)]
    assert localization.callbacks == [language_callback]
    assert theme.callbacks == [theme_callback]
