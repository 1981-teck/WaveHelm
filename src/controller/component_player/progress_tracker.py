# -*- coding: utf-8 -*-
"""progress_tracker.py
Gestisce il polling in background per il progresso della riproduzione e
il rilevamento della fine della traccia.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum, auto
import threading
import time
import uuid
from typing import Callable, Optional

from src.audio.audio_events import AudioEventType
from src.playback_observation import (
    ClockObservation, ClockOrigin, ClockValue, ProgressSnapshot, acquire_clock,
    finite_seconds, valid_source,
)
from .playback_state_manager import PlaybackStateManager, PlayerState
from src.controller.seek_observation import read_seek_observation, SeekObservation
from .queue_manager import QueueManager
from .terminal_retry import TerminalRetryGate
from src.controller.video_terminal import video_clock_owner, VideoClockOwner
from .engine_controller import EngineController

logger = logging.getLogger(__name__)

ENGINE_QUERY_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError, OSError, OverflowError)
CALLBACK_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
EVENT_PUBLISH_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
WORKER_EXCEPTIONS = ENGINE_QUERY_EXCEPTIONS


class _Completion(Enum):
    ACTIVE = auto()
    ENDED = auto()
    UNAVAILABLE = auto()
    FAILED = auto()


_MISSING = object()
_EndKey = tuple[PlayerState, int | None, int, int, int | None, str | None, int, VideoClockOwner]


@dataclass(frozen=True)
class _PlaybackContext:
    state: PlayerState
    revision: int | None
    engine: object | None
    track: object | None
    index: int | None
    path: str | None
    stable: bool
    epoch: int
    native_owner: VideoClockOwner

    @property
    def key(self) -> _EndKey:
        return (self.state, self.revision, id(self.engine), id(self.track), self.index, self.path, self.epoch, self.native_owner)


class ProgressTracker:
    """
    Esegue un thread in background per monitorare il progresso della riproduzione.
    """

    def __init__(
        self,
        state_manager: PlaybackStateManager,
        queue_manager: QueueManager,
        engine_controller: EngineController,
        event_publisher: Callable[[AudioEventType, dict[str, object]], object],
        on_track_end_callback: Callable[[], object],
    ) -> None:
        self.state_manager = state_manager
        self.queue_manager = queue_manager
        self.engine_controller = engine_controller
        self._publish_event = event_publisher
        self._on_track_end = on_track_end_callback

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._sample_lock = threading.Lock()
        self._sample_stream = uuid.uuid4().hex
        self._sample_sequence = 0
        self._run_epoch = 0
        self._latest_sample: tuple[_EndKey, ProgressSnapshot] | None = None
        self._terminal_retry = TerminalRetryGate()

        self._last_end_key: _EndKey | None = None
        self._pending_end_key: _EndKey | None = None
        self._polling_active = False
        self._poll_thread: Optional[threading.Thread] = None
        logger.debug("[ProgressTracker] Initialized")

    def start(self):
        """Avvia il thread di polling."""
        with self._lock:
            if self._polling_active:
                return

            self._run_epoch += 1
            self._stop_event.clear()
            self._polling_active = True
            self._poll_thread = threading.Thread(target=self._poll_worker, daemon=True)
            self._poll_thread.start()
            logger.info("[ProgressTracker] Polling thread started.")

    def stop(self):
        """Stop polling and invalidate cached/in-flight observations."""
        with self._lock:
            self._run_epoch += 1
            self._stop_event.set()
            self._clear_sample()
            if not self._polling_active:
                self._poll_thread = None
                return

            self._polling_active = False
            self._stop_event.set()
            thread = self._poll_thread

        # Evita join se siamo già nel thread di polling
        if thread and thread.is_alive():
            if threading.current_thread() is thread:
                logger.debug("[ProgressTracker] stop() called from polling thread; skip join.")
            else:
                thread.join(timeout=1.5)
                if thread.is_alive():
                    logger.warning("[ProgressTracker] Polling thread did not stop within timeout (best effort).")
                else:
                    logger.info("[ProgressTracker] Polling thread stopped.")

        with self._lock:
            self._poll_thread = None

    def shutdown(self):
        """Alias per teardown uniforme (PlayerController / AppController)."""
        self.stop()

    def _poll_worker(self) -> None:
        """Poll at 4 Hz; completion is a backend observation, not a time estimate.

        A failed observer never falls back to elapsed time. The worker owns its
        one-record completion latch; native calls and callbacks hold no tracker
        lock. A concurrent state/context/seek revision invalidates the result.
        Short automatic-next clips, declined UI dispatch and repeated native EOS
        retain the same interruptible 250 ms cadence. This is not a paint timer.
        """
        logger.debug("[ProgressTracker] Poll worker started (tid=%s).", threading.get_ident())
        try:
            while self._polling_active and not self._stop_event.is_set():
                if self._stop_event.wait(0.25):
                    break
                if not self._polling_active or self._stop_event.is_set():
                    break
                # EOS latches suppress duplicates; no extra sleep may delay
                # sampling a next clip started by the completion callback.
                self._poll_once()
        except WORKER_EXCEPTIONS as exc:
            logger.error("[ProgressTracker] Poll worker failed: %s", exc, exc_info=True)
        finally:
            logger.debug("[ProgressTracker] Poll worker exiting (tid=%s).", threading.get_ident())

    def _context(self) -> _PlaybackContext:
        """Freeze path with ownership; changing revision/track during capture is stale."""
        epoch = self._run_epoch
        revision = getattr(self.state_manager, "playback_revision", None)
        state = self.state_manager.state
        engine = self._engine_for(state)
        native_owner = video_clock_owner(engine)
        track = self.queue_manager.current_track
        index = getattr(self.queue_manager, "index", None)
        path = getattr(track, "path", None)
        stable = (epoch == self._run_epoch and type(state) is PlayerState and valid_source(path)
                  and (revision is None or (type(revision) is int and revision >= 0))
                  and (index is None or (type(index) is int and index >= 0))
                  and getattr(self.state_manager, "playback_revision", None) == revision
                  and self.state_manager.state is state
                  and self.queue_manager.current_track is track
                  and video_clock_owner(engine) == native_owner)
        return _PlaybackContext(state, revision, engine, track, index, path, stable, epoch, native_owner)

    def _is_current(self, context: _PlaybackContext) -> bool:
        """Bound before/after guards, not a global all-thread atomicity claim."""
        current = self._context()
        return (context.stable and current.stable and current.key == context.key
                and not self._stop_event.is_set())

    def _engine_for(self, state: PlayerState) -> object | None:
        if state in (PlayerState.PLAYING_AUDIO, PlayerState.PAUSED_AUDIO):
            return getattr(self.engine_controller, "audio_engine", None)
        if state in (PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO):
            return getattr(self.engine_controller, "video_controller", None)
        return None

    def _query_completion(self, engine: object | None) -> _Completion:
        """Validate the optional dynamic backend boundary without truthiness coercion."""
        try:
            # Shipped video separates observation from UI-owned final handoff.
            poll = getattr(engine, "observe_end", _MISSING)
            if poll is _MISSING:
                poll = getattr(engine, "poll_end", _MISSING)
            if poll is _MISSING:
                return _Completion.UNAVAILABLE
            if not callable(poll):
                return _Completion.FAILED
            ended = poll()
            if type(ended) is not bool:
                return _Completion.FAILED
            return _Completion.ENDED if ended else _Completion.ACTIVE
        except ENGINE_QUERY_EXCEPTIONS as exc:
            logger.debug("[ProgressTracker] Completion query failed: %s", exc, exc_info=True)
            return _Completion.FAILED

    def _loop_owned_by_backend(self, context: _PlaybackContext) -> bool:
        if context.state == PlayerState.PLAYING_AUDIO:
            return bool(getattr(context.engine, "_current_play_uses_native_loop", False))
        if context.state == PlayerState.PLAYING_VIDEO:
            return bool(getattr(context.engine, "loop_enabled", getattr(self.state_manager, "_loop", False)))
        return False

    def _legacy_completion(self, engine: object | None, position: float, duration: float) -> bool:
        """Compatibility only when poll_end is absent; never anticipates duration.

        An optional activity query can veto elapsed-time completion. Unknown or
        failing activity is not idle. Normal shipped audio/video use poll_end.
        """
        if duration <= 0.0 or position < duration:
            return False
        try:
            playing = getattr(engine, "is_playing", _MISSING)
            if playing is _MISSING:
                return True
            return callable(playing) and playing() is False
        except ENGINE_QUERY_EXCEPTIONS as exc:
            logger.debug("[ProgressTracker] Legacy activity query failed: %s", exc, exc_info=True)
            return False

    def _poll_once(self) -> bool:
        """Sample paused clocks too, never pause-as-EOS or GUI-native polling.

        No prior display, a post-seek revision, or stopped spectrum worker must
        not starve pause input. Ownership/error checks still reject stale samples.
        """
        context = self._context()
        seek = self._seek_status(context)
        paused = context.state in (PlayerState.PAUSED_AUDIO, PlayerState.PAUSED_VIDEO)
        if (not self.state_manager.is_playing() and not paused) or self._stop_event.is_set():
            self._last_end_key = self._pending_end_key = None
            self._clear_sample()
            return False
        if (not context.stable or (context.state is PlayerState.PAUSED_VIDEO
                                   and not self._pump_paused_video(context))):
            self._clear_sample()
            return False
        if self._terminal_retry.take(self.get_progress_snapshot()):
            self._pending_end_key, self._last_end_key = context.key, None
        if self._pending_end_key == context.key and not seek.blocks_end and not paused:
            return self._dispatch_completion(context)
        self._pending_end_key = None
        if self._last_end_key == context.key:
            if context.state is PlayerState.PLAYING_VIDEO:
                self._pump_paused_video(context)  # Keep processing errors, not another EOS.
            return False  # Keep the terminal cache until the UI consumes or invalidates it.
        origin = (ClockOrigin.VIDEO_NATIVE if context.state in
                  (PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO) else ClockOrigin.AUDIO_MIXER)
        sequence = self._reserve_sample()
        clock = acquire_clock(context.engine, origin)
        if clock.source is not None and clock.source != context.path:
            clock = clock.invalidate()
        if not self._is_current(context):
            self._clear_sample(context, sequence)
            return False
        # The real video poll pumps errors before its seek/EOS gate. Do not skip
        # that processing merely because a seek is pending.
        result = _Completion.ACTIVE if paused else self._query_completion(context.engine)
        if not self._is_current(context):
            self._clear_sample(context, sequence)
            return False
        # Unknown readings cannot authorize the compatibility duration fallback.
        position, duration = clock.position.seconds, clock.duration.seconds
        ended = result is _Completion.ENDED
        if not paused and result is _Completion.UNAVAILABLE and position is not None and duration is not None:
            ended = self._legacy_completion(context.engine, position, duration)
        if not self._is_current(context):
            self._clear_sample(context, sequence)
            return False
        if not paused and ended and not self._seek_status(context).blocks_end and not self._loop_owned_by_backend(context) and context.track is not None:
            return self._record_completion(context, clock, sequence, result)
        self._publish_observation(context, clock, sequence)
        return False

    def _record_completion(self, context: _PlaybackContext, clock: ClockObservation,
                           sequence: int, result: _Completion) -> bool:
        """Preserve the terminal cache; duplicates and declined GUI dispatch stay bounded."""
        position, duration = clock.position.seconds, clock.duration.seconds
        self._publish_observation(context, clock, sequence, terminal=result is _Completion.ENDED)
        if self._last_end_key == context.key:
            return False
        logger.info("[ProgressTracker] Completion accepted (%s; pos=%s, dur=%s).",
                    "native" if result is _Completion.ENDED else "legacy-at-duration", position, duration)
        self._pending_end_key = context.key
        return self._dispatch_completion(context)

    def request_terminal_retry(self, sample: ProgressSnapshot) -> bool:
        """GUI response may request a bounded retry of presentation, not native EOS."""
        return (self.get_progress_snapshot() is sample
                and self._terminal_retry.request(sample))

    def _seek_status(self, context: _PlaybackContext) -> SeekObservation:
        if context.state in (PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO):
            return read_seek_observation(context.engine, context.path)
        return SeekObservation()

    def _pump_paused_video(self, context: _PlaybackContext) -> bool:
        """Paused seeking still needs error dispatch and fresh clock samples."""
        try:
            pump = getattr(context.engine, 'pump_pending_events', None)
            return pump is None or (callable(pump) and pump() is True)
        except ENGINE_QUERY_EXCEPTIONS:
            logger.debug('Paused seek event pump failed.', exc_info=True)
            return False

    def _dispatch_completion(self, context: _PlaybackContext) -> bool:
        """Retain a consumed native end while UI dispatch declines an older pending job."""
        if not self._is_current(context):
            self._pending_end_key = None
            return False
        try:
            accepted = self._on_track_end()
        except CALLBACK_EXCEPTIONS as exc:
            logger.error("[ProgressTracker] Track-end callback failed: %s", exc, exc_info=True)
        else:
            if accepted is False:
                return False
        # None is accepted for legacy callbacks. Exceptions are not retried blindly.
        self._pending_end_key = None
        self._last_end_key = context.key
        return True

    @staticmethod
    def _read_seconds(engine: object | None, method: str) -> float:
        """Read optional display metadata; invalid values never imply completion."""
        try:
            getter = getattr(engine, method, None)
            if callable(getter):
                result = float(getter())
                if math.isfinite(result) and result >= 0.0:
                    return result
        except ENGINE_QUERY_EXCEPTIONS:
            logger.debug("[ProgressTracker] %s failed.", method, exc_info=True)
        return 0.0

    def get_duration(self) -> float:
        return self._read_seconds(self._engine_for(self.state_manager.state), "get_duration")

    def get_position(self) -> float:
        return self._read_seconds(self._engine_for(self.state_manager.state), "get_position")

    def _reserve_sample(self) -> int:
        """Order acquisitions before native work, not after a delayed return."""
        with self._sample_lock:
            self._sample_sequence += 1
            return self._sample_sequence

    def _clear_sample(
        self, context: _PlaybackContext | None = None, sequence: int | None = None,
    ) -> None:
        """Discard only the owned old slot, never a newer acquisition or context."""
        with self._sample_lock:
            retained = self._latest_sample
            if retained is not None and context is not None:
                if retained[0] != context.key or (sequence is not None and retained[1].sequence > sequence):
                    return
            self._latest_sample = None

    def get_progress_snapshot(self, max_age: float = 1.0) -> ProgressSnapshot | None:
        """Read a cached sample only: no native query or legacy scalar fallback.

        Source/seek/stop changes discard the old slot. An expired same-context
        sample retains its identity but both numbers become explicitly stale.
        """
        age_limit = finite_seconds(max_age)
        if age_limit is None:
            raise ValueError("Invalid sample age limit")
        with self._sample_lock:
            retained = self._latest_sample
        if retained is None:
            return None
        key, sample = retained
        context = self._context()
        if not context.stable or context.key != key or self._stop_event.is_set():
            return None
        now = time.monotonic()
        if not sample.terminal and (now < sample.clock.finished_at or now - sample.clock.started_at > age_limit):
            from dataclasses import replace
            return replace(sample, clock=sample.clock.invalidate())
        return sample

    def _publish_observation(
        self, context: _PlaybackContext, clock: ClockObservation, sequence: int | None = None,
        *, terminal: bool = False,
    ) -> None:
        """Bind event identity to acquisition, not a newly reread queue path."""
        if not self._is_current(context):
            self._clear_sample(context, sequence)
            return
        if clock.source is not None and clock.source != context.path:
            clock = clock.invalidate()
        if sequence is None:
            sequence = self._reserve_sample()
        sample = ProgressSnapshot(f"{self._sample_stream}:{context.epoch}", sequence, context.revision,
                                  context.state.name, context.path, context.index, clock, terminal)
        if not self._is_current(context):
            self._clear_sample(context, sequence)
            return
        with self._sample_lock:
            if self._latest_sample is not None and self._latest_sample[1].sequence >= sequence:
                return
            self._latest_sample = (context.key, sample)
        payload = sample.legacy_payload()
        if payload is None or not self._is_current(context):
            return
        try:
            self._publish_event(AudioEventType.PLAYBACK_PROGRESS, payload)
        except EVENT_PUBLISH_EXCEPTIONS:
            logger.debug("[ProgressTracker] publish_event failed.", exc_info=True)

    def _publish_progress(self, position: float, duration: float) -> None:
        """Historical local projection entry; production uses the bound clock route."""
        now = time.monotonic()
        clock = ClockObservation(ClockValue.read(position), ClockValue.read(duration, duration=True),
                                 now, now, ClockOrigin.LEGACY)
        self._publish_observation(self._context(), clock)
