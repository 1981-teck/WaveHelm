"""Submitted previews are drawing-only, never observed clocks or input authority."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from tests.cursor_chain_fakes import ui, tick, rig
from tests.gesture_fakes import GestureEvent
from src.controller.component_player.playback_state_manager import PlayerState
from src.playback_observation import ReadingStatus


@pytest.mark.parametrize('target', [0, 40, 160, 200])
@pytest.mark.parametrize('outcome', ['queued', 'refused', 'native-failed'])
def test_submitted_preview_holds_only_while_request_is_accepted(ui, target, outcome):
    r = ui
    epoch = r.player.state_manager.playback_epoch
    if outcome == 'refused':
        r.manager._com_ready_event.clear()
    r.widget._on_progress_slider_pointer_down(GestureEvent(x=target))
    r.widget._on_progress_slider_pointer_up(GestureEvent(x=target))
    if outcome == 'native-failed':
        r.hr = 0x80004005
        r.manager._drain_tasks_com_thread()
        tick(r)
    expected = round((target / 200.0) * 1000) if outcome == 'queued' else 400
    assert r.widget.progress_slider.GetValue() == expected
    assert r.widget._display_reading.position is None
    assert r.widget._display_reading.status is not ReadingStatus.KNOWN
    assert r.player.state_manager.playback_epoch == epoch
    owner, view, reading = r.widget._presentation.input_state()
    from src.ui_wx.seek_gesture import eligible
    assert not eligible(view, reading)


@pytest.mark.parametrize('mode', ['error', 'metadata', 'missing'])
def test_transient_capture_freezes_and_recovers_without_a_zero_write(ui, monkeypatch, mode):
    r = ui
    original = r.player.get_progress_snapshot
    def disturbed():
        if mode == 'error':
            raise RuntimeError('test capture error')
        if mode == 'metadata':
            r.player.current_track.title = 'changed title'
        return original() if mode == 'metadata' else None
    monkeypatch.setattr(r.player, 'get_progress_snapshot', disturbed)
    tick(r)
    assert r.widget.progress_slider.GetValue() == 400
    assert r.widget._display_reading.status is not ReadingStatus.KNOWN
    assert not any(item['slider'] == 0 for item in r.writes)
    monkeypatch.setattr(r.player, 'get_progress_snapshot', original)
    tick(r)
    assert r.widget.progress_slider.GetValue() == 400
    assert r.widget._display_reading.position == 4.0


def test_unknown_view_during_drag_cancels_without_submitting_or_false_zero(ui, monkeypatch):
    r = ui
    r.widget._on_progress_slider_pointer_down(GestureEvent(x=160))
    monkeypatch.setattr(r.player, 'get_playback_view', lambda: None)
    tick(r)
    assert not r.widget._seek_gesture.active
    assert r.widget.progress_slider.GetValue() == 400
    assert not r.calls and r.manager._task_queue.empty()
    assert not r.widget._presentation.input_is_current


@pytest.mark.parametrize('transition', ['stop', 'new-path', 'same-file-replay', 'controller-replaced'])
def test_visual_history_is_not_transferred_to_another_playback(ui, transition):
    r = ui; state = r.player.state_manager
    if transition == 'stop':
        state.update_state(PlayerState.STOPPED)
    elif transition == 'controller-replaced':
        replacement = SimpleNamespace(get_playback_view=lambda: None)
        if r.surface == 'mini':
            r.widget.player_controller = replacement
        else:
            r.widget._player_controller = replacement
    else:
        track = r.player.current_track
        if transition == 'new-path':
            track.path = 'new.mp4'
        state.set_context([track], 0, track)
    tick(r)
    assert r.widget.progress_slider.GetValue() == 0
    assert r.widget._display_reading.position is None


def test_valid_zero_and_backward_clock_are_not_monotonically_clamped(ui):
    r = ui
    for position in (2.0, 0.0, 3.0):
        r.position = position; r.now[0] += .25
        r.tracker._poll_once(); tick(r)
        assert r.widget.progress_slider.GetValue() == round(position * 100)
        assert r.widget._display_reading.position == position


def test_pending_history_is_cleared_on_same_file_replay(ui):
    r = ui
    r.widget._seek_from_progress_ratio(.8)
    assert r.widget.progress_slider.GetValue() == 800  # Submitted preview, not an observed clock.
    state = r.player.state_manager
    state.set_context([r.player.current_track], 0, r.player.current_track)
    tick(r)
    assert r.widget.progress_slider.GetValue() == 0
