"""Real shared widget handlers over explicit test-owned clocks/native endpoints.

The control records every thumb write, not just the final value. PRE exercises
an unchanged 08AV source copy. These tests are not native wx/Windows playback.
Edge cases: old/equal-time samples, failed or replaced seeks, teardown/reentrancy,
and bounded missing confirmation must never fabricate a clock or retain capture.
"""
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest

from src.controller.component_player.engine_controller import EngineController
from src.controller.component_player.playback_state_manager import PlayerState as S
from src.playback_observation import ClockOrigin, ReadingStatus
from src.model.media_file import MediaType
from src.video.seek_receipt import SeekSlot, NativeSeekResult, SeekPhase as P
from tests.test_ui_cached_progress import rig as base_rig, tick, flush
from tests.test_ui_progress_gestures import begin, release
from tests.gesture_fakes import GestureEvent as Event


@pytest.fixture(params=[('mini', S.PLAYING_AUDIO), ('mini', S.PAUSED_AUDIO),
                        ('mini', S.PLAYING_VIDEO), ('mini', S.PAUSED_VIDEO),
                        ('overlay', S.PLAYING_VIDEO), ('overlay', S.PAUSED_VIDEO)],
                ids=['mini-audio-play', 'mini-audio-pause', 'mini-video-play',
                     'mini-video-pause', 'overlay-video-play', 'overlay-video-pause'])
def click_rig(request: pytest.FixtureRequest,
              monkeypatch: pytest.MonkeyPatch):
    """Fixture boundary: reuse fake widgets, real facade/controller/tracker and cache."""
    surface, state = request.param
    fixture = base_rig.__wrapped__(SimpleNamespace(param=surface), monkeypatch)
    r = next(fixture)
    now = [time.monotonic() + 0.01]
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    r.widget._presentation._clock = time.monotonic
    video = state in (S.PLAYING_VIDEO, S.PAUSED_VIDEO)
    r.queue.current_track.media_type = MediaType.VIDEO if video else MediaType.AUDIO
    r.manager.set_context([r.queue.current_track], 0, r.queue.current_track)
    r.manager.update_state(state)
    observe = r.backend.observe_progress
    r.backend.observe_progress = lambda: replace(
        observe(), origin=ClockOrigin.VIDEO_NATIVE if video else ClockOrigin.AUDIO_MIXER)
    r.backend.pump_pending_events = lambda: True
    calls, operations = [], []
    slot = SeekSlot()
    def submit(target: float) -> bool:
        calls.append(target)
        if video:
            op = slot.reserve('clip.mp4', 1, slot.epoch, target)
            assert op is not None
            op.mark_queued()
            assert op.begin_native()
            op.put_nowait(NativeSeekResult(P.NATIVE_ACCEPTED_UNCONFIRMED, 0))
            operations.append(op)
        return True
    r.backend.seek = submit
    if video:
        r.backend.get_seek_receipt = lambda: operations[-1].snapshot() if operations else None
    engine = EngineController(r.manager, r.backend, r.backend)
    r.player.engine_controller = r.tracker.engine_controller = engine
    r.tracker._poll_once(); tick(r); flush(r)
    r.widget.progress_slider.SetClientSize((201, 24))
    writes = []
    original = r.widget.progress_slider.SetValue
    def write(value: int) -> None:
        writes.append(value)
        original(value)
    monkeypatch.setattr(r.widget.progress_slider, 'SetValue', write)
    r.now, r.calls, r.operations, r.writes = now, calls, operations, writes
    r.video, r.initial_state = video, state
    yield r
    with pytest.raises(StopIteration):
        next(fixture)


def complete(r: SimpleNamespace, position: float, *, delay: float = 0.05) -> None:
    """Explicit synthetic completion; a subsequent real tracker sample supplies position."""
    r.now[0] += delay
    if r.operations:
        op = r.operations[-1]
        op.observe_event(16, 1, 0, 'clip.mp4')
        op.observe_event(17, 1, 0, 'clip.mp4')
    r.now[0] += 0.05
    r.readings.position = position
    r.tracker._poll_once(); tick(r); flush(r)


@pytest.mark.parametrize('x,target', [(160, 8.0), (40, 2.0), (0, 0.0), (200, 10.0)])
def test_click_has_no_old_thumb_between_preview_and_observation(click_rig: SimpleNamespace,
                                                               x: int, target: float) -> None:
    r = click_rig
    begin(r, x=x); release(r, x=x)
    for _ in range(4):
        tick(r); flush(r)
    assert r.calls == [target]
    assert r.writes == [int(target * 100)], r.writes
    assert r.widget._display_reading.position is None  # No post-seek clock yet.
    assert r.manager.state is r.initial_state
    assert not r.widget._dragging and not r.widget.progress_slider.HasCapture()
    complete(r, target)
    assert r.widget.progress_slider.GetValue() == int(target * 100)
    assert r.widget._current_position == target
    assert 400 not in r.writes


def test_native_observation_may_differ_from_selected_target(click_rig: SimpleNamespace) -> None:
    r = click_rig
    begin(r, x=160); release(r, x=160)
    complete(r, 7.8)
    assert r.writes == [800, 780]
    assert r.widget._current_position == 7.8


def test_focus_loss_after_forwarding_does_not_undo_submitted_preview(click_rig: SimpleNamespace) -> None:
    r = click_rig
    begin(r, x=160); release(r, x=160)
    r.widget._on_progress_focus_lost(Event())
    r.widget._on_progress_capture_lost(Event())
    tick(r)
    assert r.writes == [800]
    complete(r, 8.0)
    assert r.widget._current_position == 8.0


def test_synchronous_rejection_restores_old_clock(click_rig: SimpleNamespace) -> None:
    r = click_rig
    r.backend.seek = lambda target: False
    begin(r, x=160); release(r, x=160)
    assert r.widget.progress_slider.GetValue() == 400
    assert r.widget._current_position != 8.0
    assert not r.widget.progress_slider.HasCapture()


def test_shutdown_cannot_keep_or_repaint_a_handoff(click_rig: SimpleNamespace) -> None:
    r = click_rig
    begin(r, x=160); release(r, x=160)
    r.widget.close()
    before = list(r.writes)
    tick(r); flush(r)
    assert r.writes == before
    assert r.widget._presentation._closed


def test_reentrant_cache_refresh_during_dispatch_does_not_rewind(click_rig: SimpleNamespace) -> None:
    r = click_rig
    submit = r.backend.seek
    def reenter(target: float) -> bool:
        tick(r); flush(r)
        return submit(target)
    r.backend.seek = reenter
    begin(r, x=160); release(r, x=160)
    assert r.writes == [800]
    complete(r, 8.0)
    assert r.writes == [800]


def test_equal_time_sample_cannot_end_handoff(click_rig: SimpleNamespace) -> None:
    r = click_rig
    begin(r, x=160); release(r, x=160)
    r.readings.position = 8.0
    if r.operations:
        op = r.operations[-1]
        op.observe_event(16, 1, 0, 'clip.mp4'); op.observe_event(17, 1, 0, 'clip.mp4')
    r.tracker._poll_once(); tick(r); flush(r)
    assert r.widget._display_reading.preview_ratio == 0.8
    assert r.widget._presentation._seek_handoff is not None
    assert r.writes == [800]
    r.now[0] += 0.25
    r.tracker._poll_once(); tick(r); flush(r)
    assert r.widget._current_position == 8.0
    assert r.writes == [800]


@pytest.mark.parametrize('mutation', ['stop', 'loading', 'state', 'replay', 'revision', 'source', 'owner'])
def test_unrelated_context_change_releases_preview(click_rig: SimpleNamespace, mutation: str) -> None:
    r = click_rig
    begin(r, x=160); release(r, x=160)
    if mutation == 'stop': r.manager.update_state(S.STOPPED)
    elif mutation == 'loading': r.manager.update_state(S.LOADING)
    elif mutation == 'state':
        r.manager.update_state(S.PLAYING_VIDEO if r.video else S.PLAYING_AUDIO)
    elif mutation == 'replay':
        r.manager.set_context([r.queue.current_track], 0, r.queue.current_track)
    elif mutation == 'revision': r.manager.invalidate_end_observation()
    elif mutation == 'source':
        r.queue.current_track = SimpleNamespace(path='other.mp4', title='Other', media_type=MediaType.VIDEO)
        r.manager.set_context([r.queue.current_track], 0, r.queue.current_track)
    else:
        replacement = SimpleNamespace(get_playback_view=r.player.get_playback_view)
        if r.surface == 'mini': r.widget.player_controller = replacement
        else: r.widget._player_controller = replacement
    tick(r); flush(r)
    assert r.widget._presentation._seek_handoff is None
    assert r.calls == [8.0]
    assert not r.widget.progress_slider.HasCapture()


@pytest.mark.parametrize('offset', [10.0, 11.0, -1.0])
def test_handoff_deadline_or_backward_time_cannot_renew_preview(click_rig: SimpleNamespace,
                                                             offset: float) -> None:
    r = click_rig
    begin(r, x=160); release(r, x=160)
    origin = r.now[0]
    for step in range(1, 10):
        r.now[0] = origin + step * 0.1
        tick(r); flush(r)
        assert r.writes == [800]
    r.now[0] = origin + offset
    tick(r); flush(r)
    assert r.widget._presentation._seek_handoff is None
    assert r.widget.progress_slider.GetValue() != 800
    assert r.calls == [8.0]


def test_reentrant_destroy_during_dispatch_drops_handoff(click_rig: SimpleNamespace) -> None:
    r = click_rig
    def close_during_submit(target: float) -> bool:
        r.widget.close()
        return True
    r.backend.seek = close_during_submit
    begin(r, x=160); release(r, x=160)
    assert r.widget._closed and r.widget._presentation._seek_handoff is None
    assert r.writes == [800]


def test_failure_observation_releases_without_claiming_target(click_rig: SimpleNamespace) -> None:
    r = click_rig
    begin(r, x=160); release(r, x=160)
    if r.operations:
        r.operations[-1].cancel('Explicit unit cancellation')
    else:
        observe = r.backend.observe_progress
        r.backend.observe_progress = lambda: replace(
            observe(), position=type(observe().position).unknown(ReadingStatus.ERROR))
    r.now[0] += 0.05
    r.tracker._poll_once(); tick(r); flush(r)
    assert r.widget._presentation._seek_handoff is None
    assert r.widget._current_position != 8.0
    assert r.widget._display_reading.preview_ratio is None
    assert r.widget.progress_slider.GetValue() == 400  # Never retain failed intent as history.


def test_second_deliberate_seek_after_observation_has_no_rewind(click_rig: SimpleNamespace) -> None:
    r = click_rig
    begin(r, x=160); release(r, x=160)
    complete(r, 8.0)
    r.writes.clear()
    begin(r, x=40); release(r, x=40)
    tick(r); flush(r)
    assert r.writes == [200]
    complete(r, 2.0)
    assert r.calls == [8.0, 2.0]
    assert r.writes == [200]


def test_capture_end_duplicates_do_not_dispatch_twice(click_rig: SimpleNamespace) -> None:
    r = click_rig
    begin(r, x=160); release(r, x=160)
    for _ in range(3):
        release(r, x=160)
        r.widget._on_progress_slider_changed(Event(position=800))
        r.widget._on_progress_key_up(Event())
    assert r.calls == [8.0] and r.writes == [800]


@pytest.mark.parametrize('change', ['generation', 'duration', 'missing_view'])
def test_replaced_clock_or_missing_cache_never_confirms_intent(click_rig: SimpleNamespace,
                                                              change: str) -> None:
    r = click_rig
    begin(r, x=160); release(r, x=160)
    if change == 'missing_view':
        r.player.get_playback_view = lambda: None
        tick(r)
        assert not r.widget._presentation.input_is_current
        assert r.widget._display_reading.position is None
        assert r.widget._display_reading.preview_ratio == .8
        r.now[0] += 10.0
    else:
        complete(r, 8.0)
        begin(r, x=40); release(r, x=40)
        observe = r.backend.observe_progress
        if change == 'generation':
            r.backend.observe_progress = lambda: replace(observe(), generation=2)
        else:
            r.readings.duration = 20.0
            if r.operations:
                r.operations[-1].observe_event(16, 1, 0, 'clip.mp4')
                r.operations[-1].observe_event(17, 1, 0, 'clip.mp4')
        r.now[0] += .25
        r.tracker._poll_once()
    tick(r); flush(r)
    assert r.widget._presentation._seek_handoff is None


def test_preview_is_not_committed_to_measured_visual_history(click_rig: SimpleNamespace) -> None:
    r = click_rig
    history = r.widget._presentation._last_visual
    begin(r, x=160); release(r, x=160)
    assert r.widget._presentation._last_visual == history == .4
    assert r.widget._display_reading.visual_ratio == .8
    assert r.widget._display_reading.position is None
    if r.operations:
        assert r.operations[-1].snapshot().phase is P.NATIVE_ACCEPTED_UNCONFIRMED
    complete(r, 7.8)
    assert r.widget._display_reading.preview_ratio is None
    assert r.widget._presentation._last_visual == .78
