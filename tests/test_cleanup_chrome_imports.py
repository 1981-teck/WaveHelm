"""Conservative chrome cleanup: imports only, inherited view contracts retained."""
from __future__ import annotations

import importlib
import inspect
from typing import Any, Callable, get_type_hints

import pytest

from src.ui_wx import mini_player, mini_player_chrome as chrome
from tests.test_wx_mini_player import build_mini_player


@pytest.mark.parametrize(
    'name',
    ('importlib', 'Callable', 'AudioEventType', 'unregister_callback', 'format_time', 'resolve_track_title'),
)
def test_chrome_no_longer_exposes_selected_unused_import(name: str) -> None:
    assert name not in vars(chrome)


def test_chrome_annotations_still_resolve_with_actual_globals() -> None:
    assert chrome.Any is Any
    assert get_type_hints(chrome) == {}
    methods = [value for value in vars(chrome.MiniPlayerChrome).values()
               if inspect.isfunction(value)]
    assert len(methods) == 17
    for method in methods:
        assert isinstance(get_type_hints(method, globalns=vars(chrome)), dict)
    hints = get_type_hints(chrome.MiniPlayerChrome._apply_volume_payload)
    assert hints['payload'] == dict[str, Any]


def test_public_owner_inherits_the_same_chrome_methods() -> None:
    assert mini_player.MiniPlayerChrome is chrome.MiniPlayerChrome
    assert chrome.MiniPlayerChrome in mini_player.MiniPlayer.__mro__
    for name, method in vars(chrome.MiniPlayerChrome).items():
        if inspect.isfunction(method):
            assert getattr(mini_player.MiniPlayer, name) is method


def test_public_owner_keeps_its_used_imports_and_annotations() -> None:
    assert mini_player.importlib is importlib
    assert mini_player.Callable is Callable
    hints = get_type_hints(mini_player.MiniPlayer._defer_ui)
    assert hints['callback'] == Callable[..., None]


def test_legacy_chrome_helpers_remain() -> None:
    """Guard retained helpers; two unconsumed private helpers retired in step07N."""
    assert callable(chrome.MiniPlayerChrome._set_enabled)
    assert callable(chrome.MiniPlayerChrome._refresh_track_and_state_text)


def test_inherited_callbacks_remain_registered_and_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget, player, bus, audio = build_mini_player(monkeypatch)
    try:
        assert widget.localization_manager.language_callbacks == [widget.update_localization]
        assert widget.theme_manager.theme_callbacks == [widget.update_theme_colors]
        widget.localization_manager.language_callbacks[0]()
        widget.theme_manager.theme_callbacks[0]()
        assert widget.play_button.label == 'Riproduci'
        assert widget.panel.background == '#161616'
        assert widget.play_button.background == '#252525'
        assert widget.progress_slider.GetValue() == 100
        assert player.calls == []
        assert audio.seek_calls == []
    finally:
        widget.close()
    assert len(bus.unsubscribed) == 8
    assert widget._progress_timer is None


@pytest.mark.parametrize('value,expected', ((0.0, 0), (0.37, 37), (1.0, 100)))
def test_inherited_volume_payload_changes_only_the_control(
    monkeypatch: pytest.MonkeyPatch, value: float, expected: int,
) -> None:
    widget, player, _, audio = build_mini_player(monkeypatch)
    try:
        payload = {'volume': value}
        widget._apply_volume_payload(payload)
        assert widget.volume_slider.GetValue() == expected
        assert payload == {'volume': value}
        assert widget._volume_sync is False
        assert player.volume_calls == []
        assert audio.volume == 0.35
    finally:
        widget.close()
