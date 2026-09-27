"""Both real GUI consumers with deterministic cached observations and test-owned wx.

No native frame/latency claim: timer, event, input, invalidation and EOS separation
are tested through production methods. Drawing never becomes measured position.
"""
from dataclasses import replace
from types import SimpleNamespace
import sys
import time

import pytest

from src.controller.component_player.playback_state_manager import PlayerState
from src.controller.component_player.engine_controller import SeekDispatch
from src.playback_observation import ClockValue, ReadingStatus
from src.ui_wx.mini_player import MiniPlayer
from src.ui_wx.video_overlay_controls import ExternalVideoControlOverlay
from tests.test_playback_view import make_source, make_sample
from tests.wx_fakes import FakeWxModule
from tests.gesture_fakes import GestureEvent


@pytest.fixture(params=['mini', 'overlay'])
def motion_ui(request, monkeypatch):
    jobs = []
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    monkeypatch.setattr(FakeWxModule, 'CallAfter', staticmethod(lambda f,*a: jobs.append(lambda: f(*a))))
    player = make_source(); now = [time.monotonic()]
    slot = [make_sample(player, stamp=now[0])]
    player.progress_tracker.get_progress_snapshot = lambda: slot[0]
    calls = []
    player.seek = lambda value: calls.append(value) or SeekDispatch.FORWARDED_UNCONFIRMED
    parent = FakeWxModule.Frame(None)
    owner = (MiniPlayer(parent, player_controller=player) if request.param == 'mini'
             else ExternalVideoControlOverlay(FakeWxModule, parent, player_controller=player))
    owner._presentation._clock = lambda: now[0]
    # Construction used the real clock; install a fresh deterministic timeline.
    owner._presentation._motion.reset()
    owner.progress_slider.SetClientSize((201,24))
    owner._poll_progress()
    def advance(position, increment=.25, **kwargs):
        now[0] += increment
        slot[0] = make_sample(player,position=position,sequence=slot[0].sequence+1,
                             stamp=now[0],**kwargs)
        owner._poll_progress()
    def tick():
        if request.param == 'mini': owner._on_progress_timer_tick()
        else: owner._on_poll_timer()
    r = SimpleNamespace(owner=owner,player=player,now=now,slot=slot,jobs=jobs,calls=calls,
                        advance=advance,tick=tick,surface=request.param)
    yield r
    owner.close()


def start(r):
    assert r.owner.progress_slider.GetValue() == 400
    r.advance(4.25)
    assert r.owner._current_position == 4.25
    assert r.owner.progress_slider.GetValue() == 400


def test_both_timer_routes_draw_intermediate_values_without_changing_measured_clock(motion_ui):
    r=motion_ui; start(r)
    original = r.slot[0]
    r.now[0] += .1; r.tick()
    assert r.owner.progress_slider.GetValue() == 410
    assert r.owner._current_position == 4.25
    assert r.owner._presentation.input_state()[2].position == 4.25
    assert r.slot[0] is original and r.calls == []
    if r.surface == 'mini':
        assert r.owner._display_reading.position == 4.25


def test_old_event_numbers_cannot_change_interpolation_endpoint(motion_ui):
    r=motion_ui; start(r)
    for _ in range(40):
        r.owner._handle_progress_event({'current_time':999.,'total_duration':1000.})
    assert len(r.jobs) == 1
    r.now[0] += .1; r.jobs.pop()()
    assert r.owner.progress_slider.GetValue() == 410
    r.now[0] += .15; r.tick()
    assert r.owner.progress_slider.GetValue() == 425


@pytest.mark.parametrize('delay', [.25, .75, 2., 60.])
def test_no_later_observation_means_no_additional_motion_or_completion(motion_ui, delay):
    r=motion_ui; start(r); r.now[0] += delay; r.tick()
    assert r.owner.progress_slider.GetValue() == (425 if delay <= 1.0 else 400)
    assert not r.calls


@pytest.mark.parametrize('state', [PlayerState.PAUSED_VIDEO,PlayerState.STOPPED,
                                   PlayerState.LOADING,PlayerState.ERROR])
def test_state_transition_cancels_segment(motion_ui,state):
    r=motion_ui; start(r); r.now[0] += .1; r.tick()
    r.player.state_manager.update_state(state); r.slot[0] = None
    r.owner._poll_progress()
    position = r.owner.progress_slider.GetValue()
    for _ in range(10): r.now[0] += .1; r.tick()
    assert r.owner.progress_slider.GetValue() == position
    assert not r.calls


@pytest.mark.parametrize('status', [ReadingStatus.ERROR,ReadingStatus.STALE,ReadingStatus.UNAVAILABLE])
def test_bad_sample_cancels_and_recovers_without_replaying_motion(motion_ui,status):
    r=motion_ui; start(r)
    r.slot[0] = replace(r.slot[0],clock=replace(r.slot[0].clock,position=ClockValue(status)))
    r.owner._poll_progress(); position=r.owner.progress_slider.GetValue()
    r.now[0]+=.1; r.tick()
    assert r.owner.progress_slider.GetValue() == position
    r.advance(4.5)
    assert r.owner.progress_slider.GetValue() == 450


@pytest.mark.parametrize('position', [0.,2.,9.])
def test_seek_revision_rebases_instead_of_animating_the_old_source_clock(motion_ui,position):
    r=motion_ui; start(r)
    r.player.state_manager.invalidate_end_observation()
    r.advance(position)
    assert r.owner.progress_slider.GetValue() == round(position*100)


def test_preview_owns_thumb_but_not_measurement_and_cancel_rebases(motion_ui):
    r=motion_ui; start(r)
    r.owner.progress_slider.trigger('EVT_LEFT_DOWN', GestureEvent(x=160))
    assert r.owner.progress_slider.GetValue() == 800
    r.advance(4.5); r.now[0]+=.1; r.tick()
    assert r.owner.progress_slider.GetValue() == 800
    assert r.owner._current_position == 4.25
    assert r.calls == []
    r.owner.progress_slider.trigger('EVT_KEY_DOWN', GestureEvent(key=27))
    assert r.owner.progress_slider.GetValue() == 450
    assert r.owner._current_position == 4.5 and r.calls == []


def test_one_release_still_sends_captured_target_not_interpolated_position(motion_ui):
    r=motion_ui; start(r)
    r.owner.progress_slider.trigger('EVT_LEFT_DOWN', GestureEvent(x=140))
    r.advance(4.5)
    r.owner.progress_slider.trigger('EVT_LEFT_UP', GestureEvent(x=160,down=False))
    assert r.calls == [8.0]
    assert r.owner._current_position == 4.5
    assert r.owner.progress_slider.GetValue() == 450
    r.owner.progress_slider.trigger('EVT_SCROLL_CHANGED', GestureEvent(position=800))
    assert r.calls == [8.0]


def test_closed_owner_never_paints_delayed_motion(motion_ui):
    r=motion_ui; start(r)
    r.owner._handle_progress_event({}); r.owner.close()
    last=r.owner.progress_slider.GetValue(); r.now[0]+=.1
    while r.jobs: r.jobs.pop()()
    r.tick()
    assert r.owner.progress_slider.GetValue() == last


def test_slow_native_clock_is_never_called_by_animation_timer(motion_ui):
    r=motion_ui; start(r)
    def forbidden(): raise AssertionError('native query from GUI')
    r.player.get_position=r.player.get_duration=forbidden
    for _ in range(10): r.now[0] += .02; r.tick()
    assert r.owner.progress_slider.GetValue() == 420


def test_cursor_only_tick_does_not_rebuild_mini_labels(motion_ui,monkeypatch):
    r=motion_ui; start(r)
    if r.surface == 'mini':
        def forbidden(): raise AssertionError('full label refresh on motion-only tick')
        monkeypatch.setattr(r.owner,'_refresh_track_and_state_text',forbidden)
    r.now[0] += .1; r.tick()
    assert r.owner.progress_slider.GetValue() == 410


def test_timer_interval_is_shared_and_native_pointer_poll_is_not_accelerated(motion_ui,monkeypatch):
    from src.ui_wx.progress_motion import FRAME_INTERVAL_MS
    r=motion_ui; calls=[]
    monkeypatch.setattr(FakeWxModule,'CallLater',staticmethod(lambda ms,fn: calls.append(ms)))
    if r.surface == 'overlay':
        native=[]
        monkeypatch.setattr(r.owner,'_monitor_pointer_activity',lambda: native.append(True))
        monkeypatch.setattr('src.ui_wx.video_overlay_controls.time.monotonic',lambda:r.now[0])
        r.owner._last_pointer_poll = r.now[0]
        for offset in [.033,.033,.033,.033]: r.now[0]+=offset; r.tick()
        assert len(native) == 1
    else:
        r.tick()
    assert calls and all(ms == FRAME_INTERVAL_MS == 33 for ms in calls)


def test_pending_seek_and_confirmed_later_sample_never_animate_the_request(motion_ui):
    from src.video.seek_receipt import SeekSlot, NativeSeekResult, SeekPhase
    r = motion_ui; start(r)
    slot = SeekSlot()
    operation = slot.reserve('clip.mp4', 1, 0, 8.0)
    operation.mark_queued(); assert operation.begin_native()
    operation.put_nowait(NativeSeekResult(SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED, 0))
    r.player.engine_controller = SimpleNamespace(video_controller=SimpleNamespace(
        get_seek_receipt=operation.snapshot))
    r.owner._poll_progress()
    assert r.owner._display_reading.seek_phase is SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED
    assert r.owner.progress_slider.GetValue() == 400
    r.now[0] += .1; r.tick()
    assert r.owner.progress_slider.GetValue() == 400
    operation.observe_event(16, 1, 0, 'clip.mp4')
    operation.observe_event(17, 1, 0, 'clip.mp4')
    r.advance(7.8)
    assert r.owner._display_reading.seek_phase is SeekPhase.NATIVE_COMPLETED
    assert r.owner._current_position == 7.8
    assert r.owner.progress_slider.GetValue() == 780
    assert r.calls == []


def test_error_in_seek_receipt_cancels_motion_without_fake_completion(motion_ui):
    r = motion_ui; start(r)
    def unavailable(): raise OSError('native receipt unavailable')
    r.player.engine_controller = SimpleNamespace(video_controller=SimpleNamespace(
        get_seek_receipt=unavailable))
    r.owner._poll_progress(); value = r.owner.progress_slider.GetValue()
    r.now[0] += .1; r.tick()
    assert r.owner.progress_slider.GetValue() == value
    assert r.owner._display_reading.status is ReadingStatus.ERROR
    assert not r.calls


def test_new_source_cannot_animate_from_prior_content(motion_ui):
    from src.model.media_file import MediaType
    r = motion_ui; start(r)
    track = SimpleNamespace(path='next.mp4',title='Next',media_type=MediaType.VIDEO)
    r.player.queue_manager.current_track = track
    r.player.state_manager.set_context([track],0,track)
    r.advance(.1)
    assert r.owner.progress_slider.GetValue() == 10
    assert r.owner._current_media_path == 'next.mp4'


def test_stationary_observation_stops_an_incomplete_segment(motion_ui):
    r = motion_ui; start(r)
    r.advance(4.25, increment=.1)
    assert r.owner.progress_slider.GetValue() == 425
    r.now[0]+=.1; r.tick()
    assert r.owner.progress_slider.GetValue() == 425


def test_actual_zero_reset_helper_clears_measured_and_rendered_pair(motion_ui):
    r = motion_ui; start(r)
    if r.surface == 'mini':
        r.owner._reset_progress_state(clear_path=True)
        assert r.owner.progress_slider.GetValue() == 0
        assert r.owner._display_reading.position is None
        assert r.owner._current_position == r.owner._current_duration == 0.0
    else:
        r.player.state_manager.update_state(PlayerState.STOPPED)
        r.owner._poll_progress()
        assert r.owner.progress_slider.GetValue() == 0


def test_near_duration_drawing_cannot_invoke_transport(motion_ui):
    r = motion_ui; r.advance(9.75)
    r.advance(10.)
    assert r.owner.progress_slider.GetValue() == 975
    r.now[0] += .1; r.tick()
    assert r.owner.progress_slider.GetValue() == 985
    r.now[0] += .15; r.tick()
    assert r.owner.progress_slider.GetValue() == 1000
    assert r.calls == []
