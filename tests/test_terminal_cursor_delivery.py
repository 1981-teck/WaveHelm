"""Native end -> tracker cache -> UI bus -> both bars -> teardown/next ordering.

Actual production methods run between test-owned wx/native/worker boundaries.
No media playback, native GUI timing or Windows DLL is claimed by these tests.
"""
from dataclasses import replace
from types import SimpleNamespace
import threading
import pytest
from tests.cursor_chain_fakes import ui, rig, tick
from src.audio.audio_event_bus import AudioEventBus
from src.audio.audio_events import AudioEventType as E
from src.controller.component_player.playback_state_manager import PlayerState
from src.playback_observation import ClockValue, ReadingStatus
from src.ui_wx.main_view import MainView


def finish_in_worker(r):
    answers = []
    worker = threading.Thread(target=lambda: answers.append(r.tracker._poll_once()))
    worker.start(); worker.join(2)
    assert not worker.is_alive() and answers == [True]
    assert r.jobs and not r.next_calls


def wire_actual_bus(r, monkeypatch):
    bus = AudioEventBus()
    r.player.event_bus = bus
    r.tracker._publish_event = bus.publish
    r.controller._publish_event = bus.publish
    if r.surface == 'mini':
        r.widget.event_bus = bus
    else:
        r.widget._event_bus = bus
    r.widget._subscribe_events()
    order = []
    def painted():
        assert r.controller._adapter is r.adapter, 'Paint precedes native teardown'
        order.append(('paint', r.widget.progress_slider.GetValue()))
    monkeypatch.setattr(r.widget.progress_slider, 'Update', painted)
    def detach():
        order.append(('release', None))
        r.controller._adapter = None
    r.controller._close_adapter_internal = detach
    main = MainView.__new__(MainView)
    main._deferred_external_window_close = False
    main._stop_hwnd_retry_timer = lambda: None
    main._update_video_status = lambda text: None
    def destroy():
        assert r.controller._adapter is None, 'Native owner must not outlive its HWND'
        order.append(('destroy', None))
        r.widget.close()
    main._external_video_window = SimpleNamespace(
        suppress_close_notification_once=lambda: None, destroy=destroy)
    bus.subscribe(E.VIDEO_PLAYBACK_ENDED, main._on_video_playback_ended)
    r.player.next = lambda: (r.next_calls.append('next'), order.append(('next', None)))
    return bus, order


@pytest.mark.parametrize('duration', [.89, 2.2613, 9.707])
def test_final_sample_is_delivered_before_native_close_and_real_mainview_handler(ui, monkeypatch, duration):
    r = ui; bus, order = wire_actual_bus(r, monkeypatch)
    try:
        r.duration = duration; r.position = max(0.0, duration - .25)
        r.now[0] += .25; r.tracker._poll_once(); tick(r)
        r.position = duration; r.ended = True; r.now[0] += .25
        finish_in_worker(r)
        assert r.controller._adapter is r.adapter
        sample = r.tracker.get_progress_snapshot()
        assert sample.terminal and sample.clock.position.seconds == duration
        # Execute actual facade/UI handoff before an ordinary refresh callback.
        r.jobs.pop(0)()
        assert order == [('paint', 1000), ('release', None), ('destroy', None), ('next', None)]
        while r.ui_jobs:
            r.ui_jobs.pop(0)()
        assert len(order) == 4
    finally:
        r.widget.close(); bus.shutdown()


@pytest.mark.parametrize('clock_kind', ['missing-position', 'missing-duration', 'failed', 'future', 'before-duration'])
def test_terminal_fact_does_not_invent_a_known_clock_pair(ui, monkeypatch, clock_kind):
    r = ui; read = r.controller.observe_progress
    def clock():
        current = read()
        if clock_kind == 'missing-position':
            return replace(current, position=ClockValue(ReadingStatus.UNAVAILABLE))
        if clock_kind == 'missing-duration':
            return replace(current, duration=ClockValue(ReadingStatus.UNAVAILABLE))
        if clock_kind == 'failed':
            raise RuntimeError('clock unavailable')
        if clock_kind == 'future':
            return replace(current, started_at=r.now[0] + 1, finished_at=r.now[0] + 1)
        return current  # Native end is independent of the earlier measured position.
    monkeypatch.setattr(r.controller, 'observe_progress', clock)
    r.ended = True; r.now[0] += .25
    finish_in_worker(r); tick(r)
    assert r.widget.progress_slider.GetValue() == 1000
    assert r.widget._display_reading.terminal
    assert not r.widget._closed
    assert r.widget._display_reading.position != 10.0
    if clock_kind != 'before-duration':
        assert r.widget._display_reading.status is not ReadingStatus.KNOWN
    assert not r.widget._seek_from_progress_ratio(.2)


@pytest.mark.parametrize('change', ['stop', 'replay', 'next-source', 'seek'])
def test_stale_terminal_handoff_cannot_close_or_paint_another_context(ui, change):
    r = ui; r.ended = True; r.position = 10.; r.now[0] += .25
    finish_in_worker(r)
    state = r.player.state_manager
    if change == 'stop':
        state.update_state(PlayerState.STOPPED)
    elif change == 'seek':
        state.invalidate_end_observation()
    else:
        if change == 'next-source':
            r.player.current_track.path = 'next.mp4'
        state.set_context([r.player.current_track], 0, r.player.current_track)
    r.jobs.pop(0)()
    assert not r.next_calls and r.controller._adapter is r.adapter
    r.widget._handle_terminal_event({'path': 'clip.mp4', 'current_time': 10.})
    assert r.widget.progress_slider.GetValue() != 1000


def test_new_seek_during_terminal_paint_invalidates_old_transition(ui, monkeypatch):
    r = ui; bus = AudioEventBus()
    r.player.event_bus = bus
    if r.surface == 'mini':
        r.widget.event_bus = bus
    else:
        r.widget._event_bus = bus
    r.widget._subscribe_events()
    monkeypatch.setattr(r.widget.progress_slider, 'Update', lambda: r.player.seek(2.))
    try:
        r.ended = True; r.position = 10.; r.now[0] += .25
        finish_in_worker(r); r.jobs.pop(0)()
        assert not r.next_calls and r.controller._adapter is r.adapter
        assert r.controller.get_seek_receipt().blocks_end
    finally:
        r.widget.close(); bus.shutdown()


def test_no_ui_dispatcher_does_not_execute_graphical_transition_on_worker(ui):
    r = ui; r.ended = True; r.position = 10.; r.now[0] += .25
    r.player._dispatch_to_ui_thread = lambda fn: False
    answers = []
    worker = threading.Thread(target=lambda: answers.append(r.tracker._poll_once()))
    worker.start(); worker.join(2)
    assert not worker.is_alive() and answers == [False]
    assert not r.next_calls and r.controller._adapter is r.adapter
    assert r.tracker.get_progress_snapshot().terminal


@pytest.mark.parametrize('age', [0., 2., 15.])
def test_terminal_cache_survives_ui_delay_without_reacquisition_or_extra_next(ui, monkeypatch, age):
    r = ui; r.ended = True; r.position = 10.; r.now[0] += .25
    finish_in_worker(r)
    sample = r.tracker.get_progress_snapshot()
    def forbidden():
        raise AssertionError('Terminal clock was acquired again')
    monkeypatch.setattr(r.controller, 'observe_progress', forbidden)
    r.now[0] += age
    assert r.tracker._poll_once() is False
    assert r.tracker.get_progress_snapshot() is sample
    tick(r)
    assert r.widget.progress_slider.GetValue() == 1000
    assert len(r.jobs) == 1 and not r.next_calls


def test_real_controller_cleanup_and_stopped_subscriber_preserve_final_order(ui, monkeypatch):
    from types import MethodType
    from src.controller import video_controller_core as core
    r = ui; bus, order = wire_actual_bus(r, monkeypatch)
    ctrl = r.controller
    ctrl._event_bus = bus
    ctrl._close_lock = threading.RLock()
    ctrl._current_hwnd, ctrl._current_duration_hint = 123, 10.0
    ctrl._last_surface_signature = ctrl._last_error_info = None
    ctrl._close_adapter_internal = MethodType(core._close_adapter_internal, ctrl)
    ctrl._publish_event = MethodType(core._publish_event, ctrl)
    ctrl._update_state = lambda value: setattr(ctrl, '_state', value)
    monkeypatch.setattr(r.adapter, 'stop', lambda: order.append(('native-stop', None)))
    monkeypatch.setattr(r.adapter, 'close', lambda: order.append(('native-close', None)))
    main = MainView.__new__(MainView)
    main._pending_video_request = {'path': 'clip.mp4'}
    main._video_session_active = True
    main._close_external_window_on_stop_request = False
    main._deferred_external_window_close_on_user_interrupt = False
    main._deferred_external_window_close = False
    main._last_video_ready_signature = (123, 'clip.mp4')
    main._stop_hwnd_retry_timer = lambda: None
    main._update_video_status = lambda text: order.append(('stopped', None))
    bus.subscribe(E.VIDEO_PLAYBACK_STOPPED, main._on_video_playback_stopped)
    try:
        r.position = 10.; r.ended = True; r.now[0] += .25
        finish_in_worker(r); r.jobs.pop(0)()
        assert order == [('paint', 1000), ('native-stop', None), ('native-close', None),
                         ('stopped', None), ('destroy', None), ('next', None)]
        assert ctrl._adapter is None and ctrl._current_hwnd is None
        assert ctrl._current_path is None and not ctrl._closing
    finally:
        r.widget.close(); bus.shutdown()
