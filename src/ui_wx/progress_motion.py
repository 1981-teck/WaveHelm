"""Bounded, causal cursor interpolation; never a playback clock or EOS authority.

Only interpolate between positions already observed, never extrapolate. Unknown,
paused/seek/gesture-blocked input snaps to the measured pair and cancels motion.
Discontinuities rebase rather than being hidden. State is GUI-owned and constant-size.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.playback_observation import (
    ClockOrigin, ProgressSnapshot, ReadingStatus, finite_seconds,
)

# Presentation policy, not native timer or decoder guarantees.
FRAME_INTERVAL_MS = 33
MAX_BLEND_SECONDS = 0.25
MAX_SAMPLE_AGE = 0.5
MAX_SAMPLE_GAP = 0.5
MAX_QUERY_SPAN = 0.1
MAX_POSITION_STEP = 0.5
MIN_SAMPLE_GAP = 0.025


class MotionMode(Enum):
    OBSERVED = 'observed'
    INTERPOLATED = 'interpolated'
    FROZEN = 'frozen'


@dataclass(frozen=True, slots=True)
class VisualProgress:
    """Drawing-only cursor; the caller retains measured position separately."""
    position: float | None
    mode: MotionMode


class ProgressMotion:
    """One finite segment, no history queue, lock, native call, timer or log I/O.

    Same-sample wakeups cannot extend a segment. Bad/regressive clocks cancel it.
    Missing/new/changed source, epoch, revision, duration or direction rebases it.
    No input target or synthetic duration is accepted as a motion endpoint.
    """
    __slots__ = ('_sample', '_from', '_target', '_start', '_span', '_last_now')

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._sample: ProgressSnapshot | None = None
        self._from = self._target = 0.0
        self._start = self._span = 0.0
        self._last_now: float | None = None

    def _snap(self, sample: ProgressSnapshot | None, position: float | None,
              now: float | None, mode: MotionMode) -> VisualProgress:
        self._sample, self._last_now = sample, now
        self._from = self._target = position if position is not None else 0.0
        self._start = now if now is not None else 0.0
        self._span = 0.0
        return VisualProgress(position, mode)

    @staticmethod
    def _usable(sample: ProgressSnapshot | None, position: float, now: float) -> bool:
        if sample is None:
            return False
        clock = sample.clock
        return (sample.state in ('PLAYING_AUDIO', 'PLAYING_VIDEO')
                and clock.origin in (ClockOrigin.AUDIO_MIXER, ClockOrigin.VIDEO_NATIVE)
                and sample.path is not None and clock.source == sample.path
                and clock.position.status is ReadingStatus.KNOWN
                and clock.duration.status is ReadingStatus.KNOWN
                and clock.position.seconds == position
                and clock.duration.seconds is not None and clock.duration.seconds > 0.0
                and 0.0 <= now - clock.finished_at
                and now - clock.started_at <= MAX_SAMPLE_AGE
                and clock.finished_at - clock.started_at <= MAX_QUERY_SPAN)

    @staticmethod
    def _continuous(previous: ProgressSnapshot, current: ProgressSnapshot) -> bool:
        a, b = previous.clock, current.clock
        identity_a = (previous.stream, previous.revision, previous.state, previous.path,
                      previous.index, a.generation, a.origin, a.duration.seconds)
        identity_b = (current.stream, current.revision, current.state, current.path,
                      current.index, b.generation, b.origin, b.duration.seconds)
        if (identity_a != identity_b or current.sequence <= previous.sequence
                or b.started_at < a.finished_at
                or a.position.seconds is None or b.position.seconds is None):
            return False
        gap = b.finished_at - a.finished_at
        advance = b.position.seconds - a.position.seconds
        return MIN_SAMPLE_GAP <= gap <= MAX_SAMPLE_GAP and 0.0 < advance <= MAX_POSITION_STEP

    def _evaluate(self, now: float) -> VisualProgress:
        if self._span <= 0.0:
            return VisualProgress(self._target, MotionMode.OBSERVED)
        fraction = min(1.0, max(0.0, (now - self._start) / self._span))
        position = min(self._target, self._from + (self._target - self._from) * fraction)
        mode = MotionMode.INTERPOLATED if fraction < 1.0 else MotionMode.OBSERVED
        return VisualProgress(position, mode)

    def advance(self, sample: ProgressSnapshot | None, measured: float | None,
                now: float, *, enabled: bool) -> VisualProgress:
        """Rebase invalid/discontinuous input; blend a recent advancing sample once.

        The blend lasts at most 250 ms, shortened by remaining sample freshness.
        Timers waking late jump directly to its endpoint: no catch-up callback queue.
        A stationary sample cancels motion, including a possible buffering plateau.
        """
        stamp, position = finite_seconds(now), finite_seconds(measured)
        if (not enabled or sample is None or stamp is None or position is None
                or (self._last_now is not None and stamp < self._last_now)
                or not self._usable(sample, position, stamp)):
            return self._snap(None, position, stamp, MotionMode.FROZEN)
        previous = self._sample
        if previous == sample:
            self._last_now = stamp
            return self._evaluate(stamp)
        if previous is None or not self._continuous(previous, sample):
            return self._snap(sample, position, stamp, MotionMode.OBSERVED)
        current = self._evaluate(stamp).position
        gap = sample.clock.finished_at - previous.clock.finished_at
        remaining = MAX_SAMPLE_AGE - (stamp - sample.clock.started_at)
        self._from = min(position, current if current is not None else position)
        self._target, self._start = position, stamp
        self._span = min(MAX_BLEND_SECONDS, gap, max(0.0, remaining))
        self._sample, self._last_now = sample, stamp
        return self._evaluate(stamp)
