"""R5G early-end regressions with production polling and test-owned backends.

No simulated backend result is evidence of decoded frames, samples or native EOS.
"""
from types import SimpleNamespace

import pytest

from src.controller.component_player.playback_state_manager import PlaybackStateManager, PlayerState
from src.controller.component_player.progress_tracker import ProgressTracker


class OneTick:
    def __init__(self):
        self.calls = 0
        self.flag = False

    def wait(self, _timeout):
        self.calls += 1
        self.flag = self.flag or self.calls >= 2
        return self.flag

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True


def tick(tracker):
    tracker._polling_active = True
    tracker._stop_event = OneTick()
    tracker._poll_worker()


def make_tracker(*, state=PlayerState.PLAYING_VIDEO, native=False, position=2.01,
                 duration=2.26, available=True, loop=False):
    manager = PlaybackStateManager()
    manager.set_loop(loop)
    manager.update_state(state)
    backend = SimpleNamespace(get_position=lambda: position, get_duration=lambda: duration,
                              _current_play_uses_native_loop=False, loop_enabled=loop)
    if available:
        backend.poll_end = lambda: native
    queue = SimpleNamespace(current_track=SimpleNamespace(path="clip.mp4"), index=0)
    ended, published = [], []
    engines = SimpleNamespace(audio_engine=backend, video_controller=backend)
    tracker = ProgressTracker(manager, queue, engines,
                              lambda *args: published.append(args), lambda: ended.append(True))
    return tracker, backend, ended, published


@pytest.mark.parametrize("state", [PlayerState.PLAYING_VIDEO, PlayerState.PLAYING_AUDIO])
@pytest.mark.parametrize("position", [2.0, 2.01, 2.02, 2.08, 2.26, 99.0])
def test_native_not_ended_overrides_elapsed_and_inaccurate_duration(state, position):
    tracker, _, ended, _ = make_tracker(state=state, position=position)
    tick(tracker)
    assert ended == []


@pytest.mark.parametrize("state", [PlayerState.PLAYING_VIDEO, PlayerState.PLAYING_AUDIO])
@pytest.mark.parametrize("answer", [None, 1, "yes", [], object()])
def test_unknown_or_non_boolean_completion_never_falls_back(state, answer):
    tracker, _, ended, _ = make_tracker(state=state, native=answer, position=100.0)
    tick(tracker)
    assert ended == []


@pytest.mark.parametrize("error", [RuntimeError, OSError, ValueError, AttributeError, TypeError])
def test_query_failure_does_not_advance_or_stop_progress(error):
    tracker, backend, ended, published = make_tracker(position=99.0)

    def fail():
        raise error("query failure")

    backend.poll_end = fail
    tick(tracker)
    assert ended == []
    assert len(published) == 1


@pytest.mark.parametrize("state", [PlayerState.PLAYING_VIDEO, PlayerState.PLAYING_AUDIO])
@pytest.mark.parametrize("duration", [0.0, 0.1, 999.0, float("nan"), float("inf")])
def test_positive_native_completion_does_not_depend_on_duration(state, duration):
    tracker, _, ended, _ = make_tracker(state=state, native=True, duration=duration)
    tick(tracker)
    assert ended == [True]


@pytest.mark.parametrize("position", [0.0, 0.1, 1.99, 2.01, 2.25])
def test_legacy_fallback_never_anticipates_the_endpoint(position):
    tracker, _, ended, _ = make_tracker(available=False, position=position)
    tick(tracker)
    assert ended == []


@pytest.mark.parametrize("position", [2.26, 3.0])
def test_legacy_fallback_accepts_reached_duration_only(position):
    tracker, _, ended, _ = make_tracker(available=False, position=position)
    tick(tracker)
    assert ended == [True]


@pytest.mark.parametrize("position,duration", [(1.0, 0.0), (float("nan"), 2.26),
    (float("inf"), 2.26), (3.0, float("inf")), (3.0, float("nan")), (-1.0, 1.0)])
def test_invalid_legacy_metadata_does_not_complete(position, duration):
    tracker, _, ended, _ = make_tracker(available=False, position=position, duration=duration)
    tick(tracker)
    assert ended == []


@pytest.mark.parametrize("active", [True, None, "unknown", 0])
def test_legacy_activity_can_veto_time_fallback(active):
    tracker, backend, ended, _ = make_tracker(available=False, position=3.0)
    backend.is_playing = lambda: active
    tick(tracker)
    assert ended == []


@pytest.mark.parametrize("state", [PlayerState.PAUSED_AUDIO, PlayerState.PAUSED_VIDEO,
                                   PlayerState.STOPPED, PlayerState.LOADING, PlayerState.ERROR])
def test_non_playing_states_do_not_consume_completion(state):
    tracker, backend, ended, _ = make_tracker(state=state, native=True)
    called = []
    backend.poll_end = lambda: called.append(True) or True
    tick(tracker)
    assert not ended and not called


def test_duplicate_native_completion_is_dispatched_once_until_new_revision():
    tracker, _, ended, _ = make_tracker(native=True)
    for _ in range(4):
        tick(tracker)
    assert ended == [True]
    tracker.state_manager.update_state(PlayerState.PLAYING_VIDEO)  # same-track replay
    tick(tracker)
    assert ended == [True, True]


@pytest.mark.parametrize("change", ["stop", "pause", "seek", "context", "engine", "track"])
def test_reentrant_context_change_invalidates_the_observed_end(change):
    tracker, backend, ended, _ = make_tracker(native=True)

    def mutate():
        if change == "stop":
            tracker.state_manager.update_state(PlayerState.STOPPED)
        elif change == "pause":
            tracker.state_manager.update_state(PlayerState.PAUSED_VIDEO)
        elif change == "seek":
            tracker.state_manager.invalidate_end_observation()
        elif change == "context":
            tracker.queue_manager.index = 1
        elif change == "engine":
            tracker.engine_controller.video_controller = SimpleNamespace()
        else:
            tracker.queue_manager.current_track = SimpleNamespace(path="other.mp4")
        return True

    backend.poll_end = mutate
    tick(tracker)
    assert ended == []


def test_stop_requested_by_query_suppresses_callback():
    tracker, backend, ended, _ = make_tracker(native=True)
    backend.poll_end = lambda: tracker._stop_event.set() or True
    tick(tracker)
    assert ended == []


def test_callback_failure_is_not_repeated_for_unchanged_context():
    tracker, _, ended, _ = make_tracker(native=True)

    def fail():
        ended.append(True)
        raise RuntimeError("dispatch failed")

    tracker._on_track_end = fail
    tick(tracker)
    tick(tracker)
    assert ended == [True]


@pytest.mark.parametrize("state", [PlayerState.PLAYING_AUDIO, PlayerState.PLAYING_VIDEO])
def test_native_loop_does_not_dispatch_even_with_positive_end(state):
    tracker, backend, ended, _ = make_tracker(state=state, native=True, loop=True)
    backend._current_play_uses_native_loop = True
    tick(tracker)
    assert not ended


def test_missing_track_does_not_dispatch():
    tracker, _, ended, _ = make_tracker(native=True)
    tracker.queue_manager.current_track = None
    tick(tracker)
    assert not ended


def test_consumed_native_end_is_retained_until_callback_accepts():
    tracker, backend, ended, _ = make_tracker(native=True)
    polls = []
    answers = iter([True, False])
    backend.poll_end = lambda: polls.append(True) or next(answers)
    ready = [False]

    def accept():
        if not ready[0]:
            return False
        ended.append(True)
        return True

    tracker._on_track_end = accept
    tick(tracker)
    ready[0] = True
    tick(tracker)
    assert polls == [True]  # native poll_end may consume/close the old engine
    assert ended == [True]


def test_pending_completion_is_discarded_when_seek_invalidates_it():
    tracker, backend, ended, _ = make_tracker(native=True)
    tracker._on_track_end = lambda: False
    tick(tracker)
    tracker.state_manager.invalidate_end_observation()
    tracker._on_track_end = lambda: ended.append(True)
    backend.poll_end = lambda: False
    tick(tracker)
    assert ended == []
