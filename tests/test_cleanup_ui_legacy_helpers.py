"""Retire two unused private chrome helpers without retiring supported UI contracts.

Edge cases: stale/malformed payloads are not authority, closed widgets cannot read
or paint, and missing/loading views keep deterministic button and path states.
Existing test-owned wx controls do not qualify native Windows playback or paint.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
import sys
from types import SimpleNamespace

import pytest

from src.controller.component_player.playback_state_manager import PlayerState
from src.controller.player_controller import PlayerController
from src.playback_observation import ProgressSnapshot
from src.ui_wx.mini_player import MiniPlayer
from src.ui_wx.mini_player_chrome import MiniPlayerChrome
from src.ui_wx.mini_player_shared import resolve_track_title
from src.ui_wx.video_overlay_controls import ExternalVideoControlOverlay
from tests.test_playback_view import make_sample, make_source
from tests.wx_fakes import FakeWxModule


@dataclass
class UiRig:
    """Own a real cache-only controller/widget pair behind the existing wx boundary."""
    widget: MiniPlayer | ExternalVideoControlOverlay
    player: PlayerController
    slot: list[ProgressSnapshot | None]
    surface: str


@pytest.fixture(params=('mini', 'overlay'))
def ui(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[UiRig]:
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    player = make_source()
    slot = [make_sample(player)]
    monkeypatch.setattr(player.progress_tracker, 'get_progress_snapshot', lambda: slot[0])
    parent = FakeWxModule.Frame(None)
    widget = (MiniPlayer(parent, player_controller=player) if request.param == 'mini'
              else ExternalVideoControlOverlay(FakeWxModule, parent, player_controller=player))
    try:
        yield UiRig(widget, player, slot, str(request.param))
    finally:
        widget.close()


def wrapper_name(surface: str, index: int) -> str:
    """Select the exact supported entry point; never call a generated unknown name."""
    state = '_apply_player_state' if surface == 'mini' else '_apply_state_payload'
    return (state, '_apply_progress_payload', '_apply_video_duration_payload')[index]


@pytest.mark.parametrize('name', ('_sync_enabled_buttons', '_extract_path'))
def test_retired_private_helper_absent_from_chrome_and_owner(name: str) -> None:
    assert name not in vars(MiniPlayerChrome)
    assert not hasattr(MiniPlayer, name)
    assert callable(MiniPlayerChrome._set_enabled)


@pytest.mark.parametrize('index', range(3), ids=('state', 'progress', 'duration'))
@pytest.mark.parametrize('payload', (None, {'path': 'stale.mp4', 'current_time': 999,
                         'duration': 999, 'play': 'enabled'}, object()),
                         ids=('missing', 'stale', 'opaque'))
def test_compatibility_wrapper_reads_current_view_not_payload(
    ui: UiRig, index: int, payload: object,
) -> None:
    ui.slot[0] = make_sample(ui.player, position=6.0, sequence=2)
    ui.widget._presentation._motion.reset()
    method = getattr(ui.widget, wrapper_name(ui.surface, index))
    assert 'Compatibility entry point' in method.__doc__
    method(payload)
    assert ui.widget._current_media_path == 'clip.mp4'
    assert ui.widget._current_position == 6.0
    assert ui.widget._current_duration == 10.0
    assert ui.widget.progress_slider.GetValue() == 600


@pytest.mark.parametrize('index', range(3), ids=('state', 'progress', 'duration'))
def test_closed_compatibility_wrapper_does_not_read_or_render(
    ui: UiRig, index: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ui.widget.close()
    before = ui.widget.progress_slider.GetValue()

    def forbidden_read() -> None:
        raise AssertionError('Closed compatibility wrapper read a controller')

    monkeypatch.setattr(ui.player, 'get_playback_view', forbidden_read)
    getattr(ui.widget, wrapper_name(ui.surface, index))({'path': 'stale.mp4'})
    assert ui.widget._closed
    assert ui.widget.progress_slider.GetValue() == before


@pytest.mark.parametrize('state', (None, PlayerState.IDLE, PlayerState.LOADING,
                         PlayerState.PAUSED_VIDEO, PlayerState.PLAYING_VIDEO,
                         PlayerState.STOPPED), ids=('empty-view', 'idle', 'loading',
                         'paused', 'playing', 'stopped'))
def test_active_view_owns_path_and_mini_buttons(
    ui: UiRig, state: PlayerState | None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ui.slot[0] = None
    if state is None:
        previous = ui.player.get_playback_view()
        assert previous is not None
        empty = replace(previous, revision=previous.revision + 1, path=None,
                        title='', state=PlayerState.IDLE, sample=None)
        monkeypatch.setattr(ui.player, 'get_playback_view', lambda: empty)
    else:
        ui.player.state_manager.update_state(state)
    ui.widget._poll_progress()
    assert ui.widget._current_media_path == ('' if state is None else 'clip.mp4')
    if isinstance(ui.widget, MiniPlayer):
        playing = state is PlayerState.PLAYING_VIDEO
        assert ui.widget.play_button.enabled is (state is not None and
               state is not PlayerState.LOADING and not playing)
        assert ui.widget.pause_button.enabled is playing
        for button in (ui.widget.stop_button, ui.widget.prev_button, ui.widget.next_button):
            assert button.enabled is (state is not None)


@pytest.mark.parametrize('track,expected', (
    (None, ''), ({}, ''), ({'title': 'Title', 'name': 'Other'}, 'Title'),
    ({'name': 'Name'}, 'Name'), ({'display_name': 'Display'}, 'Display'),
    ({'path': '/tmp/音楽.wav'}, '音楽'), (SimpleNamespace(title='Object title'), 'Object title'),
    (SimpleNamespace(path='/tmp/track.mp3'), 'track'),
), ids=('none', 'empty', 'title-first', 'name', 'display', 'unicode-path',
        'object-title', 'object-path'))
def test_public_title_helper_preserved(track: object, expected: str) -> None:
    assert resolve_track_title(track) == expected


def test_refused_cache_read_keeps_identity_but_revokes_input(
    ui: UiRig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ui.player, 'get_playback_view', lambda: None)
    ui.widget._poll_progress()
    assert ui.widget._current_media_path == 'clip.mp4'
    assert not ui.widget._presentation.input_is_current
