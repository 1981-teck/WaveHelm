"""Fault/reentrancy tests for the actual shared gesture entry points on both bars.

Losing capture, native-control failure or identity changes must not submit a seek.
The fake control memory is Python-owned; this is not a Windows event-order claim.
"""
from dataclasses import replace
from types import SimpleNamespace
import time
import pytest

from src.controller.component_player.playback_state_manager import PlayerState
from src.ui_wx.seek_gesture import GesturePhase
from tests.gesture_fakes import GestureEvent as Event
from tests.test_ui_cached_progress import rig, tick, flush
from tests.test_ui_progress_gestures import gestures, begin, release, native_commit


def fail(*args):
    raise RuntimeError('Injected wx boundary failure')


@pytest.mark.parametrize('operation', ['CaptureMouse', 'ReleaseMouse', 'GetClientSize', 'SetFocus'])
def test_native_control_error_cancels_instead_of_dispatching(gestures, monkeypatch, operation):
    r = gestures
    if operation == 'ReleaseMouse': begin(r)
    monkeypatch.setattr(r.widget.progress_slider, operation, fail)
    if operation == 'ReleaseMouse': release(r)
    else: begin(r)
    assert r.calls == [] and not r.widget._dragging and not r.widget._seek_gesture.active


def test_preview_write_failure_closes_surface_and_releases_capture(gestures, monkeypatch):
    r = gestures
    monkeypatch.setattr(r.widget.progress_slider, 'SetValue', fail)
    begin(r)
    assert r.calls == [] and r.widget._closed
    assert not r.widget._updating_progress_slider and not r.widget._dragging
    assert not r.widget.progress_slider.HasCapture()


def test_release_reentrancy_changing_revision_refuses_target(gestures, monkeypatch):
    r = gestures; begin(r)
    original = r.widget.progress_slider.ReleaseMouse
    def reentrant():
        original(); r.manager.invalidate_end_observation()
    monkeypatch.setattr(r.widget.progress_slider, 'ReleaseMouse', reentrant)
    release(r)
    assert r.calls == [] and not r.widget._dragging


def test_release_reentrancy_destroying_owner_refuses_target(gestures, monkeypatch):
    r = gestures; begin(r)
    original = r.widget.progress_slider.ReleaseMouse
    def reentrant():
        original(); r.widget.close()
    monkeypatch.setattr(r.widget.progress_slider, 'ReleaseMouse', reentrant)
    release(r)
    assert r.calls == [] and r.widget._closed and not r.widget._dragging


def test_capture_not_actually_acquired_refuses_pointer_gesture(gestures, monkeypatch):
    r = gestures
    monkeypatch.setattr(r.widget.progress_slider, 'CaptureMouse', lambda: None)
    begin(r); release(r)
    assert r.calls == [] and not r.widget._seek_gesture.active


def test_preexisting_capture_is_not_stolen_or_released(gestures):
    r = gestures
    r.widget.progress_slider._test_capture = True
    begin(r); release(r)
    assert r.calls == [] and r.widget.progress_slider.HasCapture()
    assert not r.widget._capture_owned
    r.widget.progress_slider._test_capture = False


@pytest.mark.parametrize('value', [None, True, 'x', float('nan'), float('inf'), 10**400],
                         ids=['none', 'bool', 'str', 'nan', 'inf', 'overflow'])
def test_invalid_pointer_position_never_submits(gestures, value):
    r = gestures
    r.widget._on_progress_slider_pointer_down(Event(x=value))
    release(r)
    assert r.calls == [] and not r.widget._dragging


@pytest.mark.parametrize('value', [None, True, 'x', float('nan'), float('inf'), 10**400],
                         ids=['none', 'bool', 'str', 'nan', 'inf', 'overflow'])
def test_invalid_native_scroll_value_never_submits(gestures, value):
    r = gestures; native_commit(r, value)
    assert r.calls == [] and not r.widget._dragging


@pytest.mark.parametrize('width', [0, 1, None, True, 'a', float('nan'), float('inf')])
def test_unusable_width_refuses_pointer_gesture(gestures, monkeypatch, width):
    r = gestures
    monkeypatch.setattr(r.widget.progress_slider, 'GetClientSize', lambda: (width, 24))
    begin(r); release(r)
    assert r.calls == []


def test_missing_event_methods_cancel_without_seek(gestures):
    r = gestures; begin(r)
    r.widget._on_progress_slider_pointer_up(object())
    assert r.calls == [] and not r.widget._dragging


def test_native_thumb_after_cancellation_cannot_reopen_old_gesture(gestures):
    r = gestures
    r.widget._on_progress_thumb_track(Event(position=600))
    r.widget._on_progress_focus_lost(Event())
    r.widget._on_progress_thumb_track(Event(position=700))
    native_commit(r, 800)
    assert r.calls == []


def test_forged_previous_revision_view_cannot_admit_against_retained_pair(gestures):
    r = gestures
    view = r.player.get_playback_view()
    r.player.get_playback_view = lambda: replace(view, revision=0, sample=None)
    begin(r); release(r)
    assert r.calls == []


def test_fresh_attempt_can_follow_escape_without_replaying_cancelled_end(gestures):
    r = gestures; begin(r)
    r.widget._on_progress_key_down(Event(key=27)); native_commit(r)
    assert r.calls == []
    begin(r); release(r, 40)
    assert r.calls == [2.0]


def test_forced_hidden_overlay_cancels_even_with_capture(gestures):
    r = gestures
    if r.surface == 'mini':
        assert not hasattr(r.widget, '_set_overlay_visibility')
        return
    begin(r)
    r.widget._set_overlay_visibility(False)
    release(r)
    assert r.calls == [] and not r.widget._dragging


def test_release_generated_native_notification_cannot_replace_pointer_target(gestures, monkeypatch):
    r = gestures; begin(r)
    original = r.widget.progress_slider.ReleaseMouse
    def native_end_event():
        original(); native_commit(r, 200)
    monkeypatch.setattr(r.widget.progress_slider, 'ReleaseMouse', native_end_event)
    release(r, 160)
    assert r.calls == [8.0]


def test_keyboard_release_without_native_change_cannot_leave_drag_frozen(gestures):
    r = gestures
    r.widget._on_progress_key_down(Event(key=r.widget._wx.WXK_HOME))
    r.widget.progress_slider.trigger('EVT_KEY_UP', Event(key=r.widget._wx.WXK_HOME))
    flush(r)
    assert r.calls == [] and not r.widget._dragging
    assert r.widget.progress_slider.GetValue() == 400


def test_keyup_allows_native_changed_before_deferred_cleanup(gestures):
    r = gestures
    r.widget._on_progress_key_down(Event(key=r.widget._wx.WXK_RIGHT))
    r.widget._on_progress_key_up(Event(key=r.widget._wx.WXK_RIGHT))
    native_commit(r, 600); flush(r)
    assert r.calls == [6.0] and not r.widget._dragging


def test_deferred_old_keyup_cannot_cancel_new_pointer_gesture(gestures):
    r = gestures
    r.widget._on_progress_key_down(Event(key=r.widget._wx.WXK_RIGHT))
    r.widget._on_progress_key_up(Event(key=r.widget._wx.WXK_RIGHT))
    begin(r); flush(r)
    assert r.widget._dragging
    release(r)
    assert r.calls == [8.0]


def test_many_keyups_retain_one_pending_cleanup(gestures):
    r = gestures
    r.widget._on_progress_key_down(Event(key=r.widget._wx.WXK_HOME))
    before = len(r.jobs)
    for _ in range(40): r.widget._on_progress_key_up(Event(key=r.widget._wx.WXK_HOME))
    assert len(r.jobs) == before + 1
    r.widget.close(); flush(r)
    assert r.calls == []


def test_keyup_scheduling_error_cancels_without_request(gestures, monkeypatch):
    r = gestures
    r.widget._on_progress_key_down(Event(key=r.widget._wx.WXK_HOME))
    monkeypatch.setattr(r.widget._wx, 'CallAfter', fail)
    r.widget._on_progress_key_up(Event(key=r.widget._wx.WXK_HOME))
    assert r.calls == [] and not r.widget._dragging
