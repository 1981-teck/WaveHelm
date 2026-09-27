"""Actual tracker/bus/facade/widget path with test-owned wx and clock boundaries.

Old payloads, unknown readings, drag, teardown and a blocked native reader cannot
become graphical clock authority. These are not native wx/COM timing measurements.
"""
from dataclasses import replace
from types import SimpleNamespace
import sys
import threading
import time

import pytest

from src.audio.audio_event_bus import AudioEventBus
from src.audio.audio_events import AudioEventType
from src.controller.component_player.playback_state_manager import PlaybackStateManager, PlayerState
from src.controller.component_player.progress_tracker import ProgressTracker
from src.controller.player_controller import PlayerController
from src.model.media_file import MediaType
from src.playback_observation import ClockObservation, ClockValue, ClockOrigin, ReadingStatus
from src.ui_wx.common import prepare_progress_slider, set_progress_slider_value
from src.ui_wx.mini_player import MiniPlayer
from src.ui_wx.video_overlay_controls import ExternalVideoControlOverlay
from tests.wx_fakes import FakeWxModule, FakeMouseEvent


@pytest.fixture(params=['mini', 'overlay'])
def rig(request, monkeypatch):
    jobs = []
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    def defer(callback, *args):
        jobs.append(lambda: callback(*args))
    monkeypatch.setattr(FakeWxModule, 'CallAfter', staticmethod(defer))
    manager = PlaybackStateManager()
    track = SimpleNamespace(path='clip.mp4', title='Clip', duration=12.0, media_type=MediaType.VIDEO)
    manager.set_context([track], 0, track); manager.update_state(PlayerState.PLAYING_VIDEO)
    queue = SimpleNamespace(current_track=track, index=0)
    readings = SimpleNamespace(position=4.0, duration=10.0, calls=0)
    def observe():
        readings.calls += 1
        now = time.monotonic()
        return ClockObservation(ClockValue.read(readings.position), ClockValue.read(readings.duration, duration=True),
                                now, now, ClockOrigin.VIDEO_NATIVE, queue.current_track.path, 1)
    def forbidden():
        raise AssertionError('GUI attempted a native scalar query')
    backend = SimpleNamespace(observe_progress=observe, poll_end=lambda: False,
                              get_position=forbidden, get_duration=forbidden)
    engine = SimpleNamespace(video_controller=backend, audio_engine=None)
    bus = AudioEventBus()
    tracker = ProgressTracker(manager, queue, engine, bus.publish, lambda: True)
    player = PlayerController(manager, queue, engine, tracker, None)
    player.get_duration = player.get_position = forbidden
    tracker._poll_once()
    parent = FakeWxModule.Frame(None)
    widget = (MiniPlayer(parent, player_controller=player, event_bus=bus)
              if request.param == 'mini' else ExternalVideoControlOverlay(
                  FakeWxModule, parent, player_controller=player, event_bus=bus))
    obj = SimpleNamespace(widget=widget, player=player, tracker=tracker, readings=readings,
                          manager=manager, queue=queue, backend=backend, bus=bus, jobs=jobs,
                          surface=request.param)
    yield obj
    widget.close(); bus.shutdown()


def flush(rig):
    for _ in range(10):
        if not rig.jobs: return
        rig.jobs.pop(0)()
    assert not rig.jobs, 'unbounded UI callback chain'


def tick(rig):
    if rig.surface == 'mini': rig.widget._on_progress_timer_tick()
    else: rig.widget._on_poll_timer()


def test_actual_timer_reads_cache_not_metadata_or_native_clock(rig):
    for _ in range(10): tick(rig)
    assert rig.readings.calls == 1
    assert rig.widget.progress_slider.GetValue() == 400  # Not stored 4/12.


def test_older_event_after_newer_timer_does_not_rewind(rig):
    rig.bus.publish(AudioEventType.PLAYBACK_PROGRESS,
                    {'path':'clip.mp4','current_time':3.0,'total_duration':12.0})
    rig.readings.position = 5.0; rig.tracker._poll_once(); tick(rig)
    assert rig.widget.progress_slider.GetValue() == 500
    flush(rig)
    assert rig.widget.progress_slider.GetValue() == 500


def test_many_progress_and_state_signals_share_one_pending_callback(rig):
    for value in range(40):
        rig.bus.publish(AudioEventType.PLAYBACK_PROGRESS, {'current_time':value,'duration':999.0})
        rig.bus.publish(AudioEventType.PLAYER_STATE_CHANGED, {'stopped':True,'path':'old.mp4'})
    assert len(rig.jobs) == 1
    flush(rig)
    assert rig.widget.progress_slider.GetValue() == 400
    assert rig.widget._current_media_path == 'clip.mp4'


@pytest.mark.parametrize('payload', [None,{}, {'path':'previous.mp4','current_time':9.,'total_duration':10.},
                                     {'current_time':float('inf'),'total_duration':1.}])
def test_raw_progress_payload_is_never_used_as_clock(rig,payload):
    rig.widget._handle_progress_event(payload)
    flush(rig)
    assert rig.widget.progress_slider.GetValue() == 400


def test_actual_zero_after_seek_revision_is_rendered(rig):
    rig.manager.invalidate_end_observation(); rig.readings.position = 0.0
    rig.tracker._poll_once(); flush(rig)
    assert rig.widget.progress_slider.GetValue() == 0
    assert rig.widget._current_position == 0.0 and rig.widget._current_duration == 10.0


def test_same_path_replay_cannot_be_overwritten_by_previous_generation(rig):
    rig.bus.publish(AudioEventType.PLAYBACK_PROGRESS, {'path':'clip.mp4','current_time':9.,'duration':10.})
    rig.manager.invalidate_end_observation(); rig.readings.position = .2
    rig.tracker._poll_once(); tick(rig); flush(rig)
    assert rig.widget.progress_slider.GetValue() == 20


def test_new_loading_context_clears_position_and_duration_together(rig):
    new = SimpleNamespace(path='new.mp4',title='New',duration=300.,media_type=MediaType.VIDEO)
    rig.queue.current_track = new; rig.manager.set_context([new],0,new)
    rig.manager.update_state(PlayerState.LOADING)
    rig.widget._handle_state_event({'current_track': {'path':'clip.mp4', 'title':'Old'}, 'playing':True})
    flush(rig)
    assert rig.widget._current_position == rig.widget._current_duration == 0.0
    assert rig.widget._current_media_path == 'new.mp4'


def test_old_stopped_event_cannot_stop_current_playing_display(rig):
    rig.widget._handle_state_event({'stopped': True, 'reset_progress': True})
    flush(rig)
    assert rig.widget.progress_slider.GetValue() == 400


def test_stop_is_read_from_current_state_and_late_events_cannot_restore_bar(rig):
    rig.manager.update_state(PlayerState.STOPPED)
    rig.widget._handle_progress_event({'current_time':9.,'total_duration':10.})
    flush(rig)
    assert rig.widget._current_position == rig.widget._current_duration == 0.0


def test_pause_preserves_pair_without_polling_native_and_marks_it_not_fresh(rig):
    rig.manager.update_state(PlayerState.PAUSED_VIDEO)
    tick(rig)
    assert rig.readings.calls == 1 and rig.widget.progress_slider.GetValue() == 400
    assert rig.widget._display_reading.status is not ReadingStatus.KNOWN


@pytest.mark.parametrize('field', ['position','duration'])
def test_unknown_sample_does_not_mix_fields_or_jump_to_zero(rig,field):
    setattr(rig.readings,field,None)
    rig.tracker._poll_once(); tick(rig)
    assert (rig.widget._current_position,rig.widget._current_duration) == (4.0,10.0)
    assert rig.widget._display_reading.status is ReadingStatus.UNAVAILABLE


def test_expiration_freezes_pair_without_extrapolating(rig,monkeypatch):
    snapshot = rig.tracker.get_progress_snapshot()
    monkeypatch.setattr(time,'monotonic',lambda:snapshot.clock.started_at+2.0)
    rig.widget._presentation._clock = time.monotonic
    tick(rig)
    assert rig.widget.progress_slider.GetValue() == 400
    assert rig.widget._display_reading.status is ReadingStatus.STALE


def test_drag_blocks_both_rendered_fields_and_later_refresh_uses_current_cache(rig):
    rig.widget._dragging=True
    rig.readings.position,rig.readings.duration = 8.,20.
    rig.tracker._poll_once(); flush(rig)
    assert (rig.widget._current_position,rig.widget._current_duration) == (4.,10.)
    rig.widget._dragging=False; tick(rig)
    assert (rig.widget._current_position,rig.widget._current_duration) == (8.,20.)


def test_closed_surface_rejects_pending_progress_and_state_without_widget_calls(rig):
    rig.widget._handle_progress_event({}); rig.widget._handle_state_event({})
    rig.widget.close()
    def destroyed(value): raise AssertionError('late repaint on destroyed widget')
    rig.widget.progress_slider.SetValue=destroyed
    flush(rig); tick(rig)
    assert rig.widget._presentation._closed


def test_native_widget_failure_clears_update_flag_and_performs_cleanup(rig):
    def broken(value): raise RuntimeError('native widget destroyed')
    rig.widget.progress_slider.SetValue=broken
    rig.readings.position=5.; rig.tracker._poll_once(); tick(rig)
    assert rig.widget._closed and not rig.widget._updating_progress_slider
    assert rig.widget._subscriptions == []
    assert not (rig.widget._timer_running if rig.surface=='mini' else rig.widget._poll_scheduled)
    flush(rig)


@pytest.mark.parametrize('refused',[True,False])
def test_seek_does_not_store_unconfirmed_target_in_observed_clock(rig,refused):
    calls=[]
    rig.player.seek=lambda seconds: calls.append(seconds) or not refused
    rig.widget.progress_slider.SetValue(800)
    rig.widget._on_progress_slider_changed()
    assert calls==[8.0]
    assert rig.widget._current_position==4.0
    assert rig.widget.progress_slider.GetValue()==(400 if refused else 800)


def test_blocked_tracker_observation_does_not_block_gui_timer(rig):
    entered,release=threading.Event(),threading.Event()
    original=rig.backend.observe_progress
    def blocked():
        entered.set()
        assert release.wait(3), 'test worker release missing'
        return original()
    rig.backend.observe_progress=blocked
    worker=threading.Thread(target=rig.tracker._poll_once)
    worker.start(); assert entered.wait(2)
    try:
        tick(rig)
        assert worker.is_alive() and rig.widget.progress_slider.GetValue()==400
    finally:
        release.set(); worker.join(3)
    assert not worker.is_alive()


def test_progress_worker_only_queues_gui_work(rig):
    writes=[]
    original=rig.widget.progress_slider.SetValue
    rig.widget.progress_slider.SetValue=lambda value: (writes.append(threading.get_ident()),original(value))[-1]
    rig.readings.position=5.0
    worker=threading.Thread(target=rig.tracker._poll_once); worker.start(); worker.join(3)
    assert not worker.is_alive() and not writes
    flush(rig)
    assert writes==[threading.get_ident()]


def test_missing_or_invalid_current_view_never_falls_back_to_legacy_getters(rig):
    rig.player.get_playback_view=None
    tick(rig)
    assert rig.widget._current_position==rig.widget._current_duration==0.0
    assert rig.readings.calls==1


def test_timer_creation_failure_closes_surface_without_stuck_schedule_flag(rig,monkeypatch):
    def failed(*args): raise RuntimeError('GUI event loop stopped')
    monkeypatch.setattr(FakeWxModule,'CallLater',staticmethod(failed))
    tick(rig)
    assert rig.widget._closed and not rig.widget._subscriptions
    assert not (rig.widget._timer_running if rig.surface=='mini' else rig.widget._poll_scheduled)


def test_timer_stop_failure_cannot_interrupt_subscription_cleanup(rig):
    def failed(): raise RuntimeError('native timer already gone')
    handle=rig.widget._progress_timer if rig.surface=='mini' else rig.widget._poll_timer
    handle.Stop=failed
    rig.widget.close()
    assert rig.widget._closed and not rig.widget._subscriptions
    assert not (rig.widget._timer_running if rig.surface=='mini' else rig.widget._poll_scheduled)

def test_repeated_same_progress_value_does_not_repaint_native_slider(rig):
    writes = []
    original = rig.widget.progress_slider.SetValue
    rig.widget.progress_slider.SetValue = lambda value: (writes.append(value), original(value))[-1]
    for _ in range(20):
        rig.widget._sync_progress_slider()
    assert writes == []
    rig.widget._display_reading = replace(
        rig.widget._display_reading,
        position=5.0,
        duration=10.0,
        visual_position=5.0,
        status=ReadingStatus.KNOWN,
    )
    for _ in range(20):
        rig.widget._sync_progress_slider()
    assert writes == [500]


def test_progress_slider_helpers_are_bounded_and_best_effort():
    calls = []
    slider = SimpleNamespace(
        value=12,
        GetValue=lambda: 12,
        SetValue=lambda value: calls.append(('value', value)),
        SetDoubleBuffered=lambda enabled: calls.append(('buffered', enabled)),
    )
    prepare_progress_slider(slider)
    assert set_progress_slider_value(slider, 12) is False
    assert set_progress_slider_value(SimpleNamespace(
        GetValue=lambda: 11,
        SetValue=lambda value: calls.append(('value', value)),
    ), 12) is True
    assert calls == [('buffered', True), ('value', 12)]

