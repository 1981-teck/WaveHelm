from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import time

from src.playback_observation import (
    ClockObservation, ClockOrigin, ClockValue, empty_clock, finite_seconds,
)

VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}


def normalize_loop_position(position: float, duration: float, loop_enabled: bool) -> float:
    """Clamp and wrap a playback position when engine-level looping is active."""
    try:
        pos_f = float(position)
    except (TypeError, ValueError):
        return 0.0

    if pos_f < 0.0:
        pos_f = 0.0

    try:
        dur_f = float(duration)
    except (TypeError, ValueError):
        dur_f = 0.0

    if loop_enabled and dur_f > 0.0 and pos_f >= dur_f:
        pos_f = pos_f % dur_f

    return pos_f


class _StopSignal(Protocol):
    def is_set(self) -> bool: ...


class AudioCompletionOwner(Protocol):
    _current_file: str | None
    _playback_source_file: str | None
    _seek_base: float
    _progress_thread: object | None
    _is_paused: bool
    _current_play_uses_native_loop: bool
    _progress_stop_event: _StopSignal


class _MusicActivity(Protocol):
    def get_busy(self) -> bool: ...


class CompletionMixer(Protocol):
    music: _MusicActivity

    def get_init(self) -> object: ...


@dataclass(frozen=True)
class _AudioObservation:
    source: str | None
    render: str | None
    seek_base: float
    worker_id: int | None
    paused: bool
    native_loop: bool
    stopped: bool
    seek_serial: int = 0

    @property
    def eligible(self) -> bool:
        return bool(self.source and self.worker_id is not None and
                    not self.paused and not self.native_loop and not self.stopped
                    and self.seek_serial % 2 == 0)


def _audio_observation(owner: AudioCompletionOwner) -> _AudioObservation:
    """Copy bounded playback flags; no native calls or locks held here."""
    stopped = owner._progress_stop_event.is_set()
    if any(type(flag) is not bool for flag in
           (stopped, owner._is_paused, owner._current_play_uses_native_loop)):
        raise TypeError("invalid audio completion flags")
    serial = getattr(owner, '_audio_seek_serial', 0)
    if type(serial) is not int or serial < 0:
        raise ValueError("Invalid audio seek serial")
    worker = owner._progress_thread
    return _AudioObservation(owner._current_file, owner._playback_source_file,
                             owner._seek_base, id(worker) if worker is not None else None,
                             owner._is_paused, owner._current_play_uses_native_loop, stopped, serial)


def poll_audio_completion(owner: AudioCompletionOwner, mixer: CompletionMixer) -> bool | None:
    """Observe mixer-idle completion within an established playback lifecycle.

    Edge cases: paused/stopped/reloading is not finished; a missing mixer is
    unknown; a changed source/seek/worker invalidates an observation. Short or
    unknown-duration tracks need no prior polling sample. Per-play native loop
    owns repetition. Mixer errors propagate to the typed pygame boundary.

    Called only while the player state is PLAYING_AUDIO. A progress-worker
    reference is established after playback starts and cleared by stop/reload.
    This is not a transaction with arbitrary concurrent direct mixer callers.
    Complexity: O(1) time and space, no acquired application lock.
    """
    before = _audio_observation(owner)
    if not before.eligible:
        return False
    initialized = mixer.get_init()
    if (not isinstance(initialized, tuple) or len(initialized) != 3
            or any(type(value) is not int for value in initialized)
            or initialized[0] <= 0 or initialized[1] == 0 or initialized[2] <= 0):
        return None
    busy = mixer.music.get_busy()
    if type(busy) is not bool:
        raise TypeError("mixer activity must be boolean")
    if mixer.get_init() != initialized or _audio_observation(owner) != before:
        return None
    return not busy


class AudioClockOwner(AudioCompletionOwner, Protocol):
    """Loaded-file duration belongs to the same source as the mixer position."""

    _total_length: float


class _MusicClock(Protocol):
    def get_pos(self) -> int: ...


class ClockMixer(Protocol):
    music: _MusicClock

    def get_init(self) -> object: ...


def read_audio_clock(owner: AudioClockOwner, mixer: ClockMixer) -> ClockObservation:
    """Read a guarded mixer position/metadata-duration pair without cached fallback.

    Missing or stopped playback, missing mixer, negative elapsed sentinel, errors,
    reload/seek/worker changes and non-finite values remain distinguishable. This
    O(1) cold read neither acquires an application lock nor authorizes completion.
    Native pygame exceptions are mapped at the caller's pygame boundary.
    """
    started = time.monotonic()
    before = _audio_observation(owner)
    duration = ClockValue.read(owner._total_length, duration=True)
    # Pausing intentionally stops the spectrum worker, not the loaded mixer clock.
    # Odd serials refuse in-flight seeks; before/after comparison catches same-
    # target seek ABA, resume/reload and worker replacement without a GUI query.
    if (not before.source or before.seek_serial % 2 != 0
            or (not before.paused and (before.worker_id is None or before.stopped))):
        return empty_clock(ClockOrigin.AUDIO_MIXER, started, time.monotonic())
    initialized = mixer.get_init()
    if initialized is None:
        return empty_clock(ClockOrigin.AUDIO_MIXER, started, time.monotonic())
    if (type(initialized) is not tuple or len(initialized) != 3
            or any(type(value) is not int for value in initialized)
            or initialized[0] <= 0 or initialized[1] == 0 or initialized[2] <= 0):
        raise ValueError("Invalid mixer initialization")
    offset = finite_seconds(before.seek_base)
    if offset is None:
        raise ValueError("Invalid audio seek base")
    elapsed = mixer.music.get_pos()
    if type(elapsed) is not int or elapsed < -1:
        raise ValueError("Invalid mixer elapsed milliseconds")
    position = ClockValue.read(None if elapsed == -1 else offset + elapsed / 1000.0)
    if (before.native_loop and position.seconds is not None and duration.seconds is not None):
        position = ClockValue.read(position.seconds % duration.seconds)
    result = ClockObservation(position, duration, started, time.monotonic(),
                              ClockOrigin.AUDIO_MIXER, before.source)
    if (mixer.get_init() != initialized or _audio_observation(owner) != before
            or ClockValue.read(owner._total_length, duration=True) != duration):
        return result.invalidate()
    return result
