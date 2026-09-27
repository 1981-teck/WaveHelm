"""Real controllers/cache/receipts and widget handlers with controlled boundaries.

Only wx, owned COM tables, clocks, dispatch and native returns are test-owned.
"""
from types import MethodType
import sys
import pytest
from tests.test_native_seek_admission import rig
from tests.wx_fakes import FakeWxModule
from src.controller.component_player.progress_tracker import ProgressTracker
from src.controller.video_controller_core import observe_progress, VideoState
from src.ui_wx.mini_player import MiniPlayer
from src.ui_wx.video_overlay_controls import ExternalVideoControlOverlay


def prepare_backend(r, monkeypatch):
    r.position, r.duration, r.ended = 4.0, 10.0, False
    r.events, r.jobs, r.ui_jobs, r.next_calls = [], [], [], []
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    monkeypatch.setattr(FakeWxModule, 'CallAfter', staticmethod(
        lambda fn, *args: r.ui_jobs.append(lambda: fn(*args))))
    monkeypatch.setattr(r.manager, 'start', lambda: None)
    old_call = r.core._call_engine_ptr_method
    def native_return(engine, name, *args):
        if name == 'GetCurrentTime':
            return r.position
        if name == 'GetDuration':
            return r.duration
        return old_call(engine, name, *args)
    monkeypatch.setattr(r.core, '_call_engine_ptr_method', native_return)
    ctrl = r.controller
    ctrl._closing, ctrl._current_path, ctrl._state = False, 'clip.mp4', VideoState.PLAYING
    ctrl.observe_progress = MethodType(observe_progress, ctrl)
    # Production observe_end/poll_end remain installed; native end/teardown are controlled.
    monkeypatch.setattr(r.adapter, 'pump_events', lambda: None)
    monkeypatch.setattr(r.adapter, 'has_ended', lambda: r.ended)
    ctrl._loop_enabled = False
    ctrl._publish_event = lambda event, payload, **kw: r.events.append((event.name, payload))
    ctrl.get_position, ctrl.get_duration = lambda: r.position, lambda: r.duration
    def detach():
        ctrl._adapter, ctrl._state = None, VideoState.STOPPED
    ctrl._close_adapter_internal = detach


def prepare_tracker(r):
    r.player._dispatch_to_ui_thread = lambda fn: r.jobs.append(fn) or True
    r.player.next = lambda: r.next_calls.append('next')
    tracker = ProgressTracker(
        r.player.state_manager, r.player.queue_manager, r.player.engine_controller,
        lambda event, data: r.events.append((event.name, data)), r.player._handle_track_end)
    r.player.progress_tracker = r.tracker = tracker
    assert tracker._poll_once() is False
    sample = tracker.get_progress_snapshot()
    assert sample.clock.position.seconds == 4.0 and sample.clock.duration.seconds == 10.0


def prepare_widget(r, monkeypatch):
    parent = FakeWxModule.Frame(None)
    r.widget = (MiniPlayer(parent, player_controller=r.player) if r.surface == 'mini'
                else ExternalVideoControlOverlay(FakeWxModule, parent, player_controller=r.player))
    r.paints = []
    # wx.Window.Update exists natively but is absent from the old minimal test fake.
    monkeypatch.setattr(r.widget.progress_slider, 'Update', lambda: r.paints.append(
        r.widget.progress_slider.GetValue()), raising=False)
    r.widget._presentation._clock = lambda: r.now[0]
    r.widget._presentation._motion.reset()
    r.widget.progress_slider.SetClientSize((201, 24))
    r.widget._poll_progress()
    assert r.widget.progress_slider.GetValue() == 400
    r.writes = []
    old_set = r.widget.progress_slider.SetValue
    def set_value(value):
        r.writes.append({'t': r.now[0], 'slider': value, 'media_position': r.position})
        old_set(value)
    monkeypatch.setattr(r.widget.progress_slider, 'SetValue', set_value)


@pytest.fixture(params=['mini', 'overlay'])
def ui(rig, monkeypatch, request):
    r = rig
    r.surface = request.param
    prepare_backend(r, monkeypatch)
    prepare_tracker(r)
    prepare_widget(r, monkeypatch)
    yield r
    r.widget.close()


def tick(r):
    if r.surface == 'mini':
        r.widget._on_progress_timer_tick()
    else:
        r.widget._on_poll_timer()
