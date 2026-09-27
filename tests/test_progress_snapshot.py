"""One current producer sample; clocks/dispatch are explicit test-owned boundaries."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Callable
import time

import pytest

from src.controller.component_player.playback_state_manager import PlaybackStateManager, PlayerState
from src.controller.component_player.progress_tracker import ProgressTracker
from src.controller.player_controller import PlayerController
from src.playback_observation import (
    ClockObservation, ClockOrigin, ClockValue, ReadingStatus,
)


class Backend:
    def __init__(self) -> None:
        self.position: object = 0.0
        self.duration: object = 10.0
        self.source = "clip.mp4"
        self.origin = ClockOrigin.VIDEO_NATIVE
        self.reads = 0
        self.ends = 0
        self.ended: bool | None = False
        self.hook: Callable[[], None] = lambda: None

    def observe_progress(self) -> ClockObservation:
        self.reads += 1
        start = time.monotonic()
        result = ClockObservation(ClockValue.read(self.position),
                                  ClockValue.read(self.duration, duration=True), start, start,
                                  self.origin, self.source, 1)
        self.hook()
        return result

    def poll_end(self) -> bool | None:
        self.ends += 1
        return self.ended

    def get_position(self) -> float:
        pytest.fail("legacy getter must not replace a strict observer")

    def get_duration(self) -> float:
        pytest.fail("metadata getter must not replace a strict observer")


def setup() -> tuple[ProgressTracker, Backend, list[dict[str, object]], list[bool]]:
    manager = PlaybackStateManager()
    manager.update_state(PlayerState.PLAYING_VIDEO)
    queue = SimpleNamespace(current_track=SimpleNamespace(path="clip.mp4", duration=99), index=0)
    backend = Backend()
    events: list[dict[str, object]] = []
    ends: list[bool] = []
    tracker = ProgressTracker(manager, queue, SimpleNamespace(video_controller=backend, audio_engine=None),
                              lambda _type, payload: events.append(payload), lambda: ends.append(True))
    return tracker, backend, events, ends


def test_strict_zero_and_duration_are_a_single_bound_observation() -> None:
    tracker, backend, events, ends = setup()
    tracker._poll_once()
    sample = tracker.get_progress_snapshot()
    assert sample.clock.position.seconds == 0.0
    assert sample.clock.duration.seconds == 10.0  # Not stored 99.
    assert sample.path == "clip.mp4" and sample.revision == 1 and sample.index == 0
    assert events[0]["progress_snapshot"] == sample.to_payload()
    assert backend.reads == backend.ends == 1
    assert not ends


def test_single_slot_and_preacquisition_sequence_not_position_monotonicity() -> None:
    tracker, backend, events, _ = setup()
    for position in [4.0, 4.2, 0.0, 2.0]:
        backend.position = position
        tracker._poll_once()
    assert [event["sample_sequence"] for event in events] == [1, 2, 3, 4]
    assert len({event["sample_stream"] for event in events}) == 1
    assert tracker.get_progress_snapshot().clock.position.seconds == 2.0
    assert tracker.get_progress_snapshot().sequence == 4
    assert events[2]["current_time"] == 0.0


@pytest.mark.parametrize("field,value", [("position", None), ("position", float("nan")),
                                         ("duration", 0.0), ("duration", float("inf"))])
def test_unknown_pair_is_retained_as_status_not_false_zero_event(field: str, value: object) -> None:
    tracker, backend, events, ends = setup()
    tracker._poll_once()
    events.clear()
    setattr(backend, field, value)
    tracker._poll_once()
    sample = tracker.get_progress_snapshot()
    assert getattr(sample.clock, field).seconds is None
    assert not events and not ends


@pytest.mark.parametrize("bad", [None, False, 0.0, {}, object()])
def test_malformed_advertised_observer_does_not_use_legacy_getters(bad: object) -> None:
    tracker, backend, events, ends = setup()
    backend.observe_progress = lambda: bad
    tracker._poll_once()
    assert tracker.get_progress_snapshot().clock.position.status is ReadingStatus.ERROR
    assert not events and not ends


def test_noncallable_or_failed_observer_is_error() -> None:
    tracker, backend, events, _ = setup()
    backend.observe_progress = None
    tracker._poll_once()
    assert tracker.get_progress_snapshot().clock.position.status is ReadingStatus.ERROR
    def fail() -> ClockObservation:
        raise OSError("native failure")
    backend.observe_progress = fail
    tracker._poll_once()
    assert tracker.get_progress_snapshot().clock.position.error_type == "OSError"
    assert not events


def test_native_end_remains_authoritative_when_clock_query_fails() -> None:
    tracker, backend, events, ends = setup()
    backend.observe_progress = lambda: None
    backend.ended = True
    tracker._poll_once()
    assert ends == [True] and not events
    sample = tracker.get_progress_snapshot()
    assert sample is not None and sample.terminal
    assert sample.clock.position.seconds is None
    assert sample.clock.duration.seconds is None


def test_absent_observer_uses_explicit_legacy_origin_with_real_zero() -> None:
    tracker, _, events, _ = setup()
    tracker.engine_controller.video_controller = SimpleNamespace(
        get_position=lambda: 0.0, get_duration=lambda: 5.0, poll_end=lambda: False)
    tracker._poll_once()
    assert tracker.get_progress_snapshot().clock.origin is ClockOrigin.LEGACY
    assert events[0]["current_time"] == 0.0


def test_legacy_error_is_not_overwritten_by_zero() -> None:
    tracker, _, events, ends = setup()
    def fail() -> float:
        raise RuntimeError("old getter error")
    tracker.engine_controller.video_controller = SimpleNamespace(get_position=fail, get_duration=lambda: 5.0)
    tracker._poll_once()
    assert tracker.get_progress_snapshot().clock.position.status is ReadingStatus.ERROR
    assert not events and not ends


def test_wrong_media_clock_cannot_authorize_legacy_completion() -> None:
    tracker, backend, events, ends = setup()
    backend.source, backend.position = "other.mp4", 20.0
    tracker.engine_controller.video_controller = SimpleNamespace(observe_progress=backend.observe_progress)
    tracker._poll_once()
    assert tracker.get_progress_snapshot().clock.position.status is ReadingStatus.STALE
    assert not events and not ends


def test_cache_accessor_never_invokes_native_backend_or_legacy_getters() -> None:
    tracker, backend, _, _ = setup()
    tracker._poll_once()
    for _ in range(50):
        assert tracker.get_progress_snapshot() is not None
    assert backend.reads == 1
    facade = PlayerController.__new__(PlayerController)
    facade._is_shutdown, facade.progress_tracker = False, tracker
    assert facade.get_progress_snapshot() is tracker.get_progress_snapshot()
    facade._is_shutdown = True
    assert facade.get_progress_snapshot() is None


@pytest.mark.parametrize("age", [True, -1, float("nan"), float("inf"), "1"])
def test_invalid_cache_age_fails_explicitly(age: object) -> None:
    with pytest.raises(ValueError, match="age"):
        setup()[0].get_progress_snapshot(age)


def test_expired_sample_is_stale_and_does_not_invent_extrapolation(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker, _, _, _ = setup()
    tracker._poll_once()
    sample = tracker.get_progress_snapshot()
    monkeypatch.setattr(time, "monotonic", lambda: sample.clock.started_at + 2.0)
    stale = tracker.get_progress_snapshot()
    assert stale.clock.position.status is stale.clock.duration.status is ReadingStatus.STALE
    assert stale.sequence == sample.sequence


def test_invalid_times_from_advertised_observer_become_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sequential calls can share one native clock tick. Make staleness explicit.
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    tracker, backend, events, _ = setup()
    old = backend.observe_progress()
    now[0] += 0.25
    assert old.started_at < now[0]
    backend.observe_progress = lambda: old
    tracker._poll_once()
    assert tracker.get_progress_snapshot().clock.position.status is ReadingStatus.STALE
    assert not events


@pytest.mark.parametrize("offset,expected", [
    (-0.25, ReadingStatus.STALE), (0.0, ReadingStatus.KNOWN), (0.25, ReadingStatus.STALE),
])
def test_clock_interval_guards_at_a_fixed_tick(monkeypatch, offset, expected) -> None:
    """Old/future data is stale; a valid zero within the current tick stays known."""
    monkeypatch.setattr(time, 'monotonic', lambda: 100.0)
    tracker, backend, events, _ = setup()
    value = backend.observe_progress()
    value = replace(value, started_at=100.0 + offset, finished_at=100.0 + offset)
    backend.observe_progress = lambda: value
    tracker._poll_once()
    result = tracker.get_progress_snapshot().clock.position
    assert result.status is expected
    assert result.seconds == (0.0 if expected is ReadingStatus.KNOWN else None)
    assert bool(events) is (expected is ReadingStatus.KNOWN)
