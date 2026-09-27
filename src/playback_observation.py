"""Typed playback observations, independent of GUI and native libraries.

Zero is data; unavailable and failed readings never carry numeric positions.
Reads cover a bounded acquisition interval, not an atomic multimedia clock.
These cold observation records never authorize end-of-stream or seek completion.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math


class ReadingStatus(Enum):
    """Validity of one clock field, separate from its numeric value."""

    KNOWN = "known"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    STALE = "stale"


class ClockOrigin(Enum):
    """How a pair was acquired; legacy reads are not native paired reads."""

    VIDEO_NATIVE = "video-native"
    AUDIO_MIXER = "audio-mixer-with-metadata-duration"
    LEGACY = "legacy-separate-getters"


def finite_seconds(value: object) -> float | None:
    """Reject booleans, coercible strings, overflow and non-finite input."""
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0.0 else None


def valid_source(source: object) -> bool:
    """Accept only an absent or bounded, NUL-free source identity."""
    return source is None or (
        type(source) is str and 0 < len(source) <= 32768 and "\0" not in source
    )


@dataclass(frozen=True, slots=True)
class ClockValue:
    """A validated clock field with bounded error context, never fake zero."""

    status: ReadingStatus
    seconds: float | None = None
    error_type: str = ""
    error_message: str = ""

    def __post_init__(self) -> None:
        if type(self.status) is not ReadingStatus:
            raise TypeError("Invalid reading status")
        if self.status is ReadingStatus.KNOWN:
            if type(self.seconds) is not float or finite_seconds(self.seconds) is None:
                raise ValueError("Known clock values must be finite nonnegative floats")
            if self.error_type or self.error_message:
                raise ValueError("Known clock values cannot carry errors")
        elif self.seconds is not None:
            raise ValueError("Unknown clock values cannot carry numbers")
        for text in (self.error_type, self.error_message):
            if type(text) is not str or len(text) > 256:
                raise ValueError("Unbounded or invalid error context")

    @classmethod
    def read(cls, raw: object, *, duration: bool = False) -> ClockValue:
        """Normalize native data; zero/NaN/+inf duration is explicitly unknown."""
        seconds = finite_seconds(raw)
        if seconds is not None and (not duration or seconds > 0.0):
            return cls(ReadingStatus.KNOWN, seconds)
        if raw is None or (duration and type(raw) in (int, float) and (
            raw == 0 or (type(raw) is float and (math.isnan(raw) or raw == math.inf))
        )):
            return cls(ReadingStatus.UNAVAILABLE)
        return cls(ReadingStatus.ERROR, error_type="InvalidClockValue")

    @classmethod
    def failed(cls, error: Exception) -> ClockValue:
        """Copy only bounded boundary diagnostics, without retaining the exception."""
        return cls(ReadingStatus.ERROR, error_type=type(error).__name__[:256],
                   error_message=str(error)[:256])


@dataclass(frozen=True, slots=True)
class ClockObservation:
    """Paired fields and a monotonic acquisition interval for one engine source."""

    position: ClockValue
    duration: ClockValue
    started_at: float
    finished_at: float
    origin: ClockOrigin
    source: str | None = None
    generation: int | None = None

    def __post_init__(self) -> None:
        if type(self.position) is not ClockValue or type(self.duration) is not ClockValue:
            raise TypeError("Clock fields must be typed readings")
        if type(self.origin) is not ClockOrigin:
            raise TypeError("Invalid clock origin")
        if (finite_seconds(self.started_at) is None or finite_seconds(self.finished_at) is None
                or self.finished_at < self.started_at):
            raise ValueError("Invalid monotonic acquisition interval")
        if not valid_source(self.source):
            raise ValueError("Invalid clock source")
        if self.generation is not None and (type(self.generation) is not int or self.generation < 0):
            raise ValueError("Invalid engine generation")

    def invalidate(self, status: ReadingStatus = ReadingStatus.STALE) -> ClockObservation:
        """Clear both fields on drift; never retain a position from a different source."""
        if status not in (ReadingStatus.UNAVAILABLE, ReadingStatus.STALE):
            raise ValueError("Invalid invalidation reason")
        missing = ClockValue(status)
        return replace(self, position=missing, duration=missing)


def empty_clock(
    origin: ClockOrigin, start: float, end: float, *,
    status: ReadingStatus = ReadingStatus.UNAVAILABLE, error: Exception | None = None,
) -> ClockObservation:
    """Create an explicitly nonnumeric result on absence, failure or ownership drift."""
    value = ClockValue.failed(error) if error is not None else ClockValue(status)
    return ClockObservation(value, value, start, end, origin)


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    """One display sample; stream/sequence/revision bind observation, not completion."""

    stream: str
    sequence: int
    revision: int | None
    state: str
    path: str | None
    index: int | None
    clock: ClockObservation
    terminal: bool = False

    def __post_init__(self) -> None:
        if type(self.terminal) is not bool:
            raise TypeError("Invalid native-terminal marker")
        if type(self.stream) is not str or not 1 <= len(self.stream) <= 64:
            raise ValueError("Invalid observation stream")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("Invalid sample sequence")
        for identifier in (self.revision, self.index):
            if identifier is not None and (type(identifier) is not int or identifier < 0):
                raise ValueError("Invalid playback identifier")
        if type(self.state) is not str or self.state not in (
            "IDLE", "LOADING", "PLAYING_AUDIO", "PAUSED_AUDIO", "PLAYING_VIDEO",
            "PAUSED_VIDEO", "STOPPED", "ERROR",
        ):
            raise ValueError("Invalid playback state")
        if not valid_source(self.path) or type(self.clock) is not ClockObservation:
            raise ValueError("Invalid bound sample")

    def to_payload(self) -> dict[str, object]:
        """Serialize explicitly at the bus boundary; keep internal records typed.

        The unchanged event bus only snapshots supported scalar/container types.
        Returning new dictionaries cannot expose aliases to the immutable cache.
        """
        def field(value: ClockValue) -> dict[str, object]:
            return {"status": value.status.value, "seconds": value.seconds,
                    "error_type": value.error_type, "error_message": value.error_message}
        return {
            "schema": "wavehelm-progress-observation-v1", "stream": self.stream,
            "sequence": self.sequence, "revision": self.revision, "state": self.state,
            "path": self.path, "index": self.index, "terminal": self.terminal,
            "clock": {"position": field(self.clock.position), "duration": field(self.clock.duration),
                      "started_at": self.clock.started_at, "finished_at": self.clock.finished_at,
                      "origin": self.clock.origin.value, "source": self.clock.source,
                      "generation": self.clock.generation},
        }

    def legacy_payload(self) -> dict[str, object] | None:
        """Project only known pairs for old consumers; unknown is not numeric zero."""
        position, duration = self.clock.position.seconds, self.clock.duration.seconds
        if position is None or duration is None or duration <= 0.0:
            return None
        return {
            "current_time": position, "total_duration": duration,
            "progress_percent": min(position / duration, 1.0) * 100.0,
            "path": self.path, "progress_snapshot": self.to_payload(),
            "sample_sequence": self.sequence, "playback_revision": self.revision,
            "sample_stream": self.stream,
        }


def acquire_clock(engine: object | None, expected_origin: ClockOrigin) -> ClockObservation:
    """Use strict capability or explicitly labelled legacy reads, never silent fallback.

    Dynamic third-party capability discovery is contained here. A failed, malformed
    or stale advertised observer cannot fall back to scalar getters. Legacy getters
    may themselves hide errors; their origin is deliberately not called native.
    """
    import time

    started = time.monotonic()
    missing = object()
    try:
        observer = getattr(engine, "observe_progress", missing)
        if observer is not missing:
            if not callable(observer):
                raise TypeError("Advertised progress observer is not callable")
            result = observer()
            if type(result) is not ClockObservation or result.origin is not expected_origin:
                raise TypeError("Progress observer returned the wrong typed clock")
            if result.started_at < started or result.finished_at > time.monotonic():
                return result.invalidate()
            return result
        def field(name: str, *, duration: bool = False) -> ClockValue:
            try:
                getter = getattr(engine, name, None)
                if getter is None:
                    return ClockValue(ReadingStatus.UNAVAILABLE)
                if not callable(getter):
                    raise TypeError("Legacy clock getter is not callable")
                return ClockValue.read(getter(), duration=duration)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError, OverflowError) as error:
                return ClockValue.failed(error)
        position = field("get_position")
        duration = field("get_duration", duration=True)
        return ClockObservation(position, duration, started, time.monotonic(), ClockOrigin.LEGACY)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, OverflowError) as error:
        return empty_clock(expected_origin, started, time.monotonic(), error=error)
