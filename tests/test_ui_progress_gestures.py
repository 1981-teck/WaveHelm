"""Both production bars with real cached facade/tracker and test-owned wx input.

Preview may not send work; cancellation and duplicate notifications cannot commit.
A changing source/duration, lost capture or refused command restores observed data.
These tests exercise control methods, not real Windows mouse or paint timing.
"""
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest

from src.controller.component_player.engine_controller import SeekDispatch
from src.controller.component_player.playback_state_manager import PlayerState
from src.controller.seek_observation import SeekObservation
from src.model.media_file import MediaType
from src.ui_wx.seek_gesture import GesturePhase
from tests.gesture_fakes import GestureEvent as Event
from tests.test_ui_cached_progress import rig, tick, flush


@pytest.fixture
def gestures(rig):
    calls = []
    rig.player.seek = lambda seconds: calls.append(seconds) or SeekDispatch.FORWARDED_UNCONFIRMED
    rig.calls = calls
    rig.widget.progress_slider.SetClientSize((201, 24))
    return rig


def begin(rig, x=120):
    rig.widget.progress_slider.trigger('EVT_LEFT_DOWN', Event(x=x))


def release(rig, x=160):
    rig.widget.progress_slider.trigger('EVT_LEFT_UP', Event(x=x, down=False))


def native_commit(rig, value=800):
    rig.widget.progress_slider.trigger('EVT_SCROLL_CHANGED', Event(position=value))


def test_pointer_preview_never_calls_backend_and_one_release_commits(gestures):
    r = gestures; begin(r)
    assert r.calls == [] and r.widget.progress_slider.GetValue() == 600
    assert r.widget._current_position == 4.0 and r.widget._current_duration == 10.0
    for x in range(120, 160):
        r.widget.progress_slider.trigger('EVT_MOTION', Event(x=x))
    assert r.calls == [] and r.widget._seek_gesture.active
    release(r)
    assert r.calls == [8.0]
    assert r.widget.progress_slider.GetValue() == 800  # Submitted preview, not measured position.
    assert not r.widget._dragging and not r.widget.progress_slider.HasCapture()
    assert r.widget._seek_gesture.phase is GesturePhase.FORWARDED_UNCONFIRMED


def test_duplicate_release_and_native_changed_do_not_repeat_request(gestures):
    r = gestures; begin(r); release(r)
    for _ in range(4):
        release(r); native_commit(r)
    assert r.calls == [8.0]
    assert r.widget.progress_slider.capture_calls == r.widget.progress_slider.release_calls == 1
    assert 'EVT_SLIDER' not in r.widget.progress_slider._bindings
    assert 'EVT_SCROLL_THUMBRELEASE' not in r.widget.progress_slider._bindings


@pytest.mark.parametrize('event', ['EVT_KILL_FOCUS', 'EVT_MOUSE_CAPTURE_LOST', 'escape'])
def test_cancel_then_late_release_never_seeks(gestures, event):
    r = gestures; begin(r)
    if event == 'EVT_MOUSE_CAPTURE_LOST':
        r.widget.progress_slider._test_capture = False
    if event == 'escape':
        r.widget.progress_slider.trigger('EVT_KEY_DOWN', Event(key=27))
    else:
        r.widget.progress_slider.trigger(event, Event())
    release(r); native_commit(r)
    assert r.calls == [] and r.widget.progress_slider.GetValue() == 400
    assert not r.widget._dragging and not r.widget.progress_slider.HasCapture()
    if event == 'EVT_MOUSE_CAPTURE_LOST':
        assert getattr(r.widget.progress_slider, 'release_calls', 0) == 0


@pytest.mark.parametrize('field', ['revision', 'source', 'state', 'duration', 'error', 'missing', 'owner'])
def test_current_cached_context_change_cancels_anchor(gestures, field):
    r = gestures; begin(r)
    if field == 'revision':
        r.manager.invalidate_end_observation()
    elif field == 'source':
        track = SimpleNamespace(path='new.mp4', title='New', media_type=MediaType.VIDEO)
        r.queue.current_track = track; r.manager.set_context([track], 0, track)
    elif field == 'state':
        r.manager.update_state(PlayerState.LOADING)
    elif field == 'duration':
        r.readings.duration = 20.0; r.tracker._poll_once()
    elif field == 'error':
        r.backend.get_seek_receipt = lambda: (_ for _ in ()).throw(RuntimeError('lost receipt'))
    elif field == 'missing':
        r.player.get_playback_view = lambda: None
    else:
        source = SimpleNamespace(get_playback_view=r.player.get_playback_view, seek=r.player.seek)
        if r.surface == 'mini': r.widget.player_controller = source
        else: r.widget._player_controller = source
    tick(r); release(r)
    assert r.calls == [] and not r.widget._dragging
    assert not r.widget.progress_slider.HasCapture()


def test_sampling_same_context_during_preview_preserves_anchor_duration(gestures):
    r = gestures; begin(r)
    r.readings.position = 5.0; r.tracker._poll_once(); flush(r); tick(r)
    assert r.widget.progress_slider.GetValue() == 600
    assert r.widget._current_position == 4.0  # Stored model was not a preview.
    release(r)
    assert r.calls == [8.0] and r.widget.progress_slider.GetValue() == 800
    assert r.widget._current_position == 5.0  # The newer observation is not the target.


def test_refused_seek_restores_observed_position_not_target(gestures):
    r = gestures
    r.player.seek = lambda s: r.calls.append(s) or SeekDispatch.REJECTED
    begin(r); release(r)
    assert r.calls == [8.0] and r.widget.progress_slider.GetValue() == 400
    assert r.widget._seek_gesture.phase is GesturePhase.REJECTED


def test_backward_and_zero_seeks_remain_possible(gestures):
    r = gestures
    begin(r); release(r, x=0)
    begin(r); release(r, x=40)
    assert r.calls == [0.0, 2.0]
    assert r.widget._current_position == 4.0


def test_native_thumb_tracking_is_preview_until_scroll_changed(gestures):
    r = gestures
    for value in (200, 300, 700):
        r.widget.progress_slider.trigger('EVT_SCROLL_THUMBTRACK', Event(position=value))
    assert r.calls == [] and r.widget.progress_slider.GetValue() == 700
    native_commit(r, 800); native_commit(r, 800)
    assert r.calls == [8.0] and r.widget.progress_slider.GetValue() == 800


@pytest.mark.parametrize('key', ['LEFT', 'RIGHT', 'UP', 'DOWN', 'HOME', 'END', 'PAGEUP', 'PAGEDOWN'])
def test_keyboard_commits_each_explicit_navigation_once(gestures, key):
    r = gestures
    for value in (800, 200):
        event = Event(key=getattr(r.widget._wx, 'WXK_' + key))
        r.widget.progress_slider.trigger('EVT_KEY_DOWN', event)
        assert event.skipped
        native_commit(r, value); native_commit(r, value)
    assert r.calls == [8.0, 2.0]


def test_capture_disappears_without_notification_before_release(gestures):
    r = gestures; begin(r)
    r.widget.progress_slider._test_capture = False
    release(r)
    assert r.calls == [] and not r.widget._dragging


def test_button_up_seen_during_motion_cancels_without_seek(gestures):
    r = gestures; begin(r)
    r.widget.progress_slider.trigger('EVT_MOTION', Event(x=160, down=False))
    release(r)
    assert r.calls == [] and not r.widget.progress_slider.HasCapture()


def test_close_releases_owned_capture_and_stops_late_work(gestures):
    r = gestures; begin(r)
    r.widget._handle_progress_event({})
    r.widget.close(); release(r); native_commit(r); flush(r)
    assert not r.widget.progress_slider.HasCapture() and r.calls == []
    assert r.widget._presentation._closed


def test_slider_destroy_closes_owner_before_more_timer_work(gestures):
    r = gestures; begin(r)
    r.widget.progress_slider._test_capture = False
    r.widget.progress_slider.trigger('EVT_WINDOW_DESTROY', Event())
    r.widget.progress_slider.SetValue = lambda v: (_ for _ in ()).throw(AssertionError('destroyed control'))
    tick(r); release(r); flush(r)
    assert r.calls == [] and r.widget._closed and r.widget._subscriptions == []


def test_pause_retains_a_valid_anchor_without_resuming(gestures):
    r = gestures
    r.manager.update_state(PlayerState.PAUSED_VIDEO); tick(r)
    begin(r); release(r)
    assert r.calls == [8.0] and r.manager.state is PlayerState.PAUSED_VIDEO


def test_gesture_age_bound_cancels_after_time_leap(gestures, monkeypatch):
    r = gestures; begin(r)
    now = time.monotonic()
    monkeypatch.setattr('src.ui_wx.progress_gesture_wx.time.monotonic', lambda: now + 121.0)
    release(r)
    assert r.calls == [] and not r.widget._dragging


def test_overlay_does_not_auto_hide_during_drag(gestures):
    r = gestures
    if r.surface != 'overlay': return  # The mini-player has no auto-hide behavior.
    r.widget.show_temporarily(); begin(r)
    r.widget._hide_after_idle()
    assert r.widget._overlay_visible and r.widget._hide_timer is not None
    r.widget.hide_now(); release(r)
    assert r.calls == [] and not r.widget._overlay_visible
