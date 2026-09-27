"""Production polling with a virtual wait, not native media or GUI measurements.

Cover short next clips, repeated EOS, pending UI dispatch, stop, pause and looping.
Only time and backend observations are test-owned; _poll_worker is not replaced.
"""
from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from src.controller.component_player.playback_state_manager import PlaybackStateManager, PlayerState
from src.controller.component_player.progress_tracker import ProgressTracker


class VirtualStop:
    """Bound test execution independently from media duration and callback behavior."""

    def __init__(self, ticks: int = 32) -> None:
        self.now = 0.0
        self.ticks = ticks
        self.flag = False
        self.waits: list[float] = []

    def wait(self, seconds: float) -> bool:
        if self.flag:
            return True
        self.waits.append(seconds)
        self.now += seconds
        if len(self.waits) > self.ticks:
            self.flag = True
        return self.flag

    def is_set(self) -> bool:
        return self.flag

    def set(self) -> None:
        self.flag = True


class ClockBackend:
    def __init__(self, clock: VirtualStop, duration: float, started: float) -> None:
        self.clock = clock
        self.duration = duration
        self.started = started
        self.loop_enabled = False
        self._current_play_uses_native_loop = False
        self.polls = 0

    def get_position(self) -> float:
        return self.clock.now - self.started

    def get_duration(self) -> float:
        return self.duration

    def poll_end(self) -> bool:
        self.polls += 1
        return self.get_position() >= self.duration


def make_tracker(clock: VirtualStop, backend: object, callback: Callable[[], object],
                 state: PlayerState = PlayerState.PLAYING_VIDEO):
    manager = PlaybackStateManager()
    manager.update_state(state)
    queue = SimpleNamespace(index=0, current_track=SimpleNamespace(path='first.mp4'))
    engines = SimpleNamespace(audio_engine=backend, video_controller=backend)
    published: list[dict[str, object]] = []
    tracker = ProgressTracker(manager, queue, engines,
                              lambda _kind, payload: published.append(payload), callback)
    tracker._stop_event = clock
    tracker._polling_active = True
    return tracker, published


@pytest.mark.parametrize('duration', [0.6, 1.0, 1.3, 3.0])
@pytest.mark.parametrize('state', [PlayerState.PLAYING_AUDIO, PlayerState.PLAYING_VIDEO])
def test_next_media_receives_progress_on_next_regular_tick(duration, state) -> None:
    clock = VirtualStop()
    first = ClockBackend(clock, 0.1, 0.0)
    completions: list[float] = []

    def next_media() -> bool:
        completions.append(clock.now)
        if len(completions) == 2:
            clock.set()
            return True
        following = ClockBackend(clock, duration, clock.now)
        tracker.engine_controller.audio_engine = following
        tracker.engine_controller.video_controller = following
        tracker.queue_manager.current_track = SimpleNamespace(path='next.mp4')
        tracker.queue_manager.index = 1
        tracker.state_manager.update_state(state)
        return True

    tracker, published = make_tracker(clock, first, next_media, state)
    tracker._poll_worker()
    assert len(completions) == 2
    assert published[0]['path'] == 'first.mp4' and published[0]['progress_snapshot']['terminal'] is True
    progress = [item for item in published if not item['progress_snapshot']['terminal']]
    assert progress, 'The next short media must have intermediate observations'
    assert progress[0]['current_time'] == pytest.approx(0.25)
    assert all(item['path'] == 'next.mp4' for item in progress)
    assert all(0.0 < item['current_time'] < duration for item in progress)
    assert published[-1]['path'] == 'next.mp4' and published[-1]['progress_snapshot']['terminal'] is True
    assert set(clock.waits) == {0.25}


def test_repeated_native_end_is_latched_without_sleeping() -> None:
    clock = VirtualStop(6)
    calls: list[float] = []
    backend = ClockBackend(clock, 0.1, 0.0)
    tracker, published = make_tracker(clock, backend, lambda: calls.append(clock.now))
    tracker._poll_worker()
    assert calls == [0.25]
    assert len(published) == 1 and published[0]["progress_snapshot"]["terminal"] is True
    assert backend.polls == 1
    assert clock.now == 1.75
    assert set(clock.waits) == {0.25}


def test_consumed_end_retries_pending_ui_at_regular_ticks_without_repoll() -> None:
    clock = VirtualStop(6)
    attempts: list[float] = []
    polls: list[float] = []

    def ended_once() -> bool:
        polls.append(clock.now)
        return len(polls) == 1

    def dispatch() -> bool:
        attempts.append(clock.now)
        return len(attempts) >= 3

    backend = SimpleNamespace(poll_end=ended_once, get_duration=lambda: 10.0,
                              get_position=lambda: 10.0)
    tracker, _ = make_tracker(clock, backend, dispatch)
    tracker._poll_worker()
    assert attempts == [0.25, 0.5, 0.75]
    assert polls == [0.25]  # Accepted terminal sample remains cached; native EOS is not repolled.
    assert tracker._pending_end_key is None
    assert set(clock.waits) == {0.25}


def test_same_path_replay_with_new_revision_is_not_a_duplicate() -> None:
    clock = VirtualStop(6)
    calls: list[float] = []
    backend = ClockBackend(clock, 0.1, 0.0)

    def replay() -> bool:
        calls.append(clock.now)
        if len(calls) == 1:
            tracker.state_manager.update_state(PlayerState.PLAYING_VIDEO)
        return True

    tracker, _ = make_tracker(clock, backend, replay)
    tracker._poll_worker()
    assert calls == [0.25, 0.5]
    assert set(clock.waits) == {0.25}


@pytest.mark.parametrize('state', [PlayerState.PLAYING_AUDIO, PlayerState.PLAYING_VIDEO])
def test_native_loop_keeps_regular_progress_without_completion(state) -> None:
    clock = VirtualStop(6)
    calls: list[float] = []
    backend = ClockBackend(clock, 0.1, 0.0)
    backend.loop_enabled = backend._current_play_uses_native_loop = True
    tracker, published = make_tracker(clock, backend, lambda: calls.append(clock.now), state)
    tracker._poll_worker()
    assert not calls
    assert len(published) == 6
    assert set(clock.waits) == {0.25}


@pytest.mark.parametrize('stop_mode', ['event', 'active-flag', 'query-stop'])
def test_stop_never_adds_a_post_end_wait(stop_mode) -> None:
    clock = VirtualStop(6)
    calls: list[float] = []
    backend = ClockBackend(clock, 0.1, 0.0)

    def complete() -> bool:
        calls.append(clock.now)
        if stop_mode == 'event':
            clock.set()
        else:
            tracker._polling_active = False
        return True

    tracker, _ = make_tracker(clock, backend, complete)
    if stop_mode == 'query-stop':
        backend.poll_end = lambda: clock.set() or True
    tracker._poll_worker()
    assert calls == ([] if stop_mode == 'query-stop' else [0.25])
    assert clock.waits == [0.25]


def test_completion_callback_error_is_latched_without_delay_or_retry() -> None:
    clock = VirtualStop(6)
    calls: list[float] = []
    backend = ClockBackend(clock, 0.1, 0.0)

    def fail() -> object:
        calls.append(clock.now)
        raise RuntimeError('test-owned UI failure')

    tracker, _ = make_tracker(clock, backend, fail)
    tracker._poll_worker()
    assert calls == [0.25]
    assert set(clock.waits) == {0.25}


def test_pending_end_invalidated_by_seek_does_not_retry_old_context() -> None:
    clock = VirtualStop(6)
    calls: list[float] = []
    polls: list[float] = []

    def ended_once() -> bool:
        polls.append(clock.now)
        return len(polls) == 1

    def decline_then_seek() -> bool:
        calls.append(clock.now)
        tracker.state_manager.invalidate_end_observation()
        return False

    backend = SimpleNamespace(poll_end=ended_once, get_duration=lambda: 10.0,
                              get_position=lambda: 2.0)
    tracker, _ = make_tracker(clock, backend, decline_then_seek)
    tracker._poll_worker()
    assert calls == [0.25]
    assert polls == [0.25, 0.5, 0.75, 1.0, 1.25, 1.5]
    assert tracker._pending_end_key is None
