"""Value, identity and interval contracts; no GUI/native clock emulation."""
from dataclasses import FrozenInstanceError, replace
import math

import pytest

from src.playback_observation import (
    ClockObservation, ClockOrigin, ClockValue, ProgressSnapshot, ReadingStatus, empty_clock,
)


def sample() -> ProgressSnapshot:
    clock = ClockObservation(ClockValue.read(0), ClockValue.read(10, duration=True),
                             1.0, 1.1, ClockOrigin.VIDEO_NATIVE, "clip.mp4", 2)
    return ProgressSnapshot("stream", 1, 3, "PLAYING_VIDEO", "clip.mp4", 0, clock)


@pytest.mark.parametrize("value", [0, 0.0, .01, 42, 1e200])
def test_finite_positions_include_zero(value: object) -> None:
    reading = ClockValue.read(value)
    assert reading.status is ReadingStatus.KNOWN
    assert type(reading.seconds) is float
    assert reading.seconds == value


@pytest.mark.parametrize("value", [True, False, "0", [], {}, object(), -1, math.inf,
                                   -math.inf, math.nan, 10 ** 400])
def test_invalid_positions_do_not_become_zero(value: object) -> None:
    reading = ClockValue.read(value)
    assert reading.status is ReadingStatus.ERROR
    assert reading.seconds is None


@pytest.mark.parametrize("value", [None, 0, math.inf, math.nan])
def test_unavailable_durations_are_not_metadata_fallback(value: object) -> None:
    assert ClockValue.read(value, duration=True) == ClockValue(ReadingStatus.UNAVAILABLE)


def test_zero_and_identity_are_preserved_in_immutable_sample() -> None:
    current = sample()
    payload = current.legacy_payload()
    assert payload is not None
    assert payload["current_time"] == 0.0
    assert payload["playback_revision"] == 3
    assert payload["progress_snapshot"] == current.to_payload()
    with pytest.raises(FrozenInstanceError):
        current.sequence = 2


@pytest.mark.parametrize("field", ["position", "duration"])
def test_unknown_field_produces_no_numeric_legacy_event(field: str) -> None:
    current = sample()
    clock = replace(current.clock, **{field: ClockValue(ReadingStatus.ERROR)})
    assert replace(current, clock=clock).legacy_payload() is None


@pytest.mark.parametrize("status", [ReadingStatus.STALE, ReadingStatus.UNAVAILABLE])
def test_invalidation_clears_both_fields(status: ReadingStatus) -> None:
    observation = sample().clock.invalidate(status)
    assert observation.position.status is status
    assert observation.duration.status is status
    assert observation.position.seconds is observation.duration.seconds is None


@pytest.mark.parametrize("field,value", [
    ("sequence", 0), ("sequence", True), ("revision", -1), ("revision", False),
    ("state", "seeking"), ("path", "x\0y"), ("path", "x" * 32769),
    ("index", -1), ("stream", ""), ("clock", {}),
], ids=["seq-zero", "seq-bool", "rev-negative", "rev-bool", "state", "nul-path",
        "long-path", "index", "stream", "clock"])
def test_malformed_snapshot_fails(field: str, value: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        replace(sample(), **{field: value})


@pytest.mark.parametrize("field,value", [
    ("finished_at", 0.9), ("started_at", math.nan), ("started_at", True),
    ("position", 0), ("origin", "video"), ("generation", True),
])
def test_malformed_clock_fails(field: str, value: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        replace(sample().clock, **{field: value})


def test_failure_context_is_bounded_and_does_not_retain_exception() -> None:
    clock = empty_clock(ClockOrigin.LEGACY, 1.0, 2.0, error=OSError("x" * 1000))
    assert clock.position.status is ReadingStatus.ERROR
    assert clock.position.error_type == "OSError"
    assert len(clock.position.error_message) == 256
    assert clock.position.seconds is None


def test_overrun_only_clamps_presentation_and_backward_read_is_allowed() -> None:
    current = sample()
    overrun = replace(current.clock, position=ClockValue.read(20))
    assert replace(current, clock=overrun).legacy_payload()["progress_percent"] == 100.0
    assert current.legacy_payload()["progress_percent"] == 0.0
