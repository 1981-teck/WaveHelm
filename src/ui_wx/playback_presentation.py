"""Cache-only playback presentation shared by both wx progress surfaces.

Events are wakeups, never clock authority. At most one refresh is queued plus an
executing refresh. Animation is drawing-only; no native calls or EOS decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import logging
import threading
import time
from typing import Callable

from src.controller.playback_view import PlaybackView
from src.controller.component_player.playback_state_manager import PlayerState
from src.playback_observation import ReadingStatus, ProgressSnapshot, finite_seconds
from src.ui_wx.progress_motion import ProgressMotion, MotionMode
from src.video.seek_receipt import SeekPhase
from .progress_trace import ProgressTrace
from .seek_gesture import SeekHandoff

logger = logging.getLogger(__name__)
_READ_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError, OverflowError)
_ACTIVE = (PlayerState.PLAYING_AUDIO, PlayerState.PLAYING_VIDEO,
           PlayerState.PAUSED_AUDIO, PlayerState.PAUSED_VIDEO)
_PAUSE_PAIRS = (
    (PlayerState.PLAYING_AUDIO, PlayerState.PAUSED_AUDIO),
    (PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO),
    (PlayerState.PAUSED_AUDIO, PlayerState.PLAYING_AUDIO),
    (PlayerState.PAUSED_VIDEO, PlayerState.PLAYING_VIDEO),
)


@dataclass(frozen=True, slots=True)
class DisplayReading:
    """Displayed pair; retained numbers with non-KNOWN status are frozen, not fresh."""

    position: float | None = None
    duration: float | None = None
    status: ReadingStatus = ReadingStatus.UNAVAILABLE
    seek_phase: SeekPhase | None = None
    visual_position: float | None = None
    motion_mode: MotionMode = MotionMode.OBSERVED
    held_ratio: float | None = None
    terminal: bool = False
    preview_ratio: float | None = None  # Submitted intent, never observed progress.

    @property
    def visual_ratio(self) -> float:
        """Drawing-only ratio; input/EOS continue to use the measured pair."""
        position = self.visual_position if self.visual_position is not None else self.position
        duration = self.duration
        if self.terminal:
            return 1.0  # Native completion fact, not a fabricated clock measurement.
        if self.preview_ratio is not None:
            return self.preview_ratio
        if self.held_ratio is not None:
            return self.held_ratio
        if position is None or duration is None or duration <= 0:
            return 0.0
        return min(1.0, max(0.0, position / duration))

    @property
    def ratio(self) -> float:
        if self.position is None or self.duration is None or self.duration <= 0:
            return 0.0
        return min(1.0, max(0.0, self.position / self.duration))


class PlaybackPresentation:
    """Bounded presentation state owned by the GUI thread.

    request() may run on a bus worker; only scheduling flags are locked and no
    external call runs under that lock. refresh()/close() belong to the GUI thread.
    Late events retain no payload, controller replacement resets identity, and
    deferred callbacks refuse closed views. No legacy scalar getter fallback.
    """

    def __init__(
        self, source: Callable[[], object | None],
        render: Callable[[PlaybackView | None, DisplayReading], bool | None],
        dispatch: Callable[[Callable[[], None]], None] | None,
        clock: Callable[[], float] = time.monotonic, *, surface: str = "unspecified",
    ) -> None:
        self._source_reader, self._render = source, render
        self._dispatch, self._clock = dispatch, clock
        self._lock = threading.Lock()
        self._closed = False
        self._pending = False
        self._source: object | None = None
        self._view: PlaybackView | None = None
        self._sample: ProgressSnapshot | None = None
        self._display = DisplayReading()
        self._painted: tuple[PlaybackView | None, DisplayReading] | None = None
        self._read_failed = False
        self._revision_floor = -1
        self._input_current = False
        self._motion = ProgressMotion()
        self._last_visual: float | None = None
        self._last_generation: int | None = None
        self._trace = ProgressTrace(surface)
        self._seek_handoff: SeekHandoff | None = None

    def request(self) -> bool:
        """Coalesce a wakeup without copying payload or calling a widget on a worker."""
        with self._lock:
            if self._closed or self._pending or self._dispatch is None:
                return False
            self._pending = True
        try:
            self._dispatch(self._drain)
        except _READ_ERRORS:
            with self._lock:
                self._pending = False
            logger.debug('Playback refresh scheduling failed.', exc_info=True)
            return False
        return True

    def _drain(self) -> None:
        with self._lock:
            self._pending = False
            closed = self._closed
        if not closed:
            self.refresh()

    def close(self) -> None:
        """Invalidate callbacks; the surrounding widget cancels its own timer."""
        with self._lock:
            self._closed = True
            self._pending = False
        self._trace.close()
        self._seek_handoff = None
        self._source = self._view = self._sample = None
        self._last_visual = None
        self._last_generation = None
        self._motion.reset()
        self._input_current = False
        self._painted = None
        self._display = DisplayReading()

    def _reset_owner(self, source: object | None) -> None:
        """Observed owner replacement clears history, unlike a refused same-owner read."""
        self._view = self._sample = None
        self._seek_handoff = None
        self._last_visual = self._last_generation = None
        self._motion.reset()
        self._display = DisplayReading()
        self._source = source
        self._revision_floor = -1

    def _read(self) -> PlaybackView | None:
        """Contain the optional facade boundary and report errors once per failure run."""
        try:
            source = self._source_reader()
            if source is not self._source:
                self._reset_owner(source)
            reader = getattr(source, 'get_playback_view', None)
            if reader is None:
                return None
            if not callable(reader):
                raise TypeError('Playback view reader is not callable')
            view = reader()
            if self._source_reader() is not source:
                self._reset_owner(None)
                return None
            if view is not None and type(view) is not PlaybackView:
                raise TypeError('Playback view reader returned an invalid record')
            self._read_failed = False
            return view
        except _READ_ERRORS:
            if not self._read_failed:
                logger.debug('Cached playback view unavailable.', exc_info=True)
            self._read_failed = True
            return None

    def refresh(self) -> None:
        """Consume only a current view. Timer and deferred event use the same reader."""
        self._input_current = False
        if self._closed:
            return
        view = self._read()
        if self._closed:
            return
        if view is not None and view.revision < self._revision_floor:
            self._motion.reset()
            return
        if view is not None:
            self._revision_floor = view.revision
        self._accept(view)
        self._input_current = (view is not None
                               and (view.sample is None or view.sample == self._sample))
        presentation = (self._view, self._visual_reading())
        if presentation != self._painted:
            rendered = self._render(*presentation) is not False and not self._closed
            self._trace.record(self._view, self._sample, presentation[1], self._clock(),
                               current=self._input_current, painted=rendered,
                               reason=('seek-preview-pending' if presentation[1].preview_ratio is not None
                                       else 'capture-unavailable' if view is None else 'cache-refresh'))
            if rendered:
                self._painted = presentation
                if presentation[1].preview_ratio is None:
                    self._last_visual = presentation[1].visual_ratio
                    if self._sample is not None and presentation[1].status is ReadingStatus.KNOWN:
                        self._last_generation = self._sample.clock.generation
            else:
                self._motion.reset()

    def _visual_reading(self) -> DisplayReading:
        """Separate bounded cursor motion from measured data and gesture admission.

        A stale/rejected read, pending/failed seek, pause or missing sample cancels
        motion. Repeated wakeups never renew a segment's endpoint or deadline.
        """
        view = self._view
        handoff = self._seek_handoff
        if handoff is not None:
            if handoff.holds(self._source, view, self._display, self._clock(),
                             current=self._input_current):
                self._motion.reset()
                return replace(self._display, preview_ratio=handoff.target / handoff.duration,
                               motion_mode=MotionMode.FROZEN)
            self._seek_handoff = None
        if self._display.held_ratio is not None or self._display.terminal:
            self._motion.reset()
            return self._display
        enabled = (self._input_current and view is not None
                   and view.state in (PlayerState.PLAYING_AUDIO, PlayerState.PLAYING_VIDEO)
                   and self._display.status is ReadingStatus.KNOWN
                   and self._display.seek_phase in (None, SeekPhase.NATIVE_COMPLETED,
                                                   SeekPhase.REJECTED)
                   and not view.seek.blocks_end)
        visual = self._motion.advance(self._sample, self._display.position,
                                      self._clock(), enabled=enabled)
        return replace(self._display, visual_position=visual.position, motion_mode=visual.mode)

    def begin_seek_handoff(self, owner: object, view: PlaybackView, target: float,
                           duration: float | None, now: float) -> SeekHandoff | None:
        """Install a visual hold before dispatch can reenter; preserve measured data."""
        if (self._closed or finite_seconds(target) is None or finite_seconds(now) is None
                or duration is None or finite_seconds(duration) is None
                or duration <= 0.0 or target > duration):
            return None
        handoff = SeekHandoff(owner, view, target, duration, now)
        self._seek_handoff = handoff
        return handoff

    def finish_seek_handoff(self, handoff: SeekHandoff | None,
                            accepted: bool, now: float) -> None:
        """A forwarding result only retains intent, never certifies a clock/seek."""
        if handoff is not None and self._seek_handoff is handoff:
            handoff.returned_at = now
            if not accepted or self._closed:
                self._seek_handoff = None

    def cancel_seek_handoff(self) -> None:
        """A fresh deliberate gesture supersedes this local visual intent."""
        self._seek_handoff = None

    def note_preview(self, ratio: float) -> None:
        """Diagnostic intent only; never changes measured/cache/visual-history state."""
        self._trace.record(self._view, self._sample, self._display, self._clock(),
                           current=self._input_current, painted=True,
                           reason='gesture-preview', preview_ratio=ratio)

    def refresh_terminal(self) -> None:
        """UI-owned final wakeup reads the current cache, never event payload numbers."""
        if threading.current_thread() is not threading.main_thread() or self._closed:
            return
        view = self._read()
        if view is not None and view.sample is not None and view.sample.terminal:
            self.refresh()

    @property
    def input_is_current(self) -> bool:
        """An ignored/regressive view is never fresh admission authority."""
        return self._input_current and not self._closed

    @property
    def source_owner(self) -> object | None:
        """GUI-only identity of the last guarded cache reader; no native call."""
        return self._source

    def invalidate_paint(self) -> None:
        """A gesture may move the native thumb without changing the measured model."""
        self._painted = None
        self._motion.reset()

    def input_state(self) -> tuple[object | None, PlaybackView | None, DisplayReading]:
        """Refresh cache-only state for GUI input admission, never scalar/native getters."""
        self.refresh()
        if not self.input_is_current:
            return self._source, None, DisplayReading()
        return self._source, self._view, self._display

    def repaint(self) -> None:
        """Undo a preview even when the last measured pair did not change."""
        self._painted = None
        self._motion.reset()
        self.refresh()

    def _hold_visual(self, status: ReadingStatus, phase: SeekPhase | None = None,
                     *, keep_pair: bool = False) -> None:
        """Freeze only acknowledged drawn history, never preview or input authority."""
        prior = self._last_visual
        self._motion.reset()
        position = self._display.position if keep_pair else None
        duration = self._display.duration if keep_pair else None
        self._display = DisplayReading(
            position, duration, status, phase,
            None, MotionMode.FROZEN, prior,
        )

    def _accept(self, view: PlaybackView | None) -> None:
        previous = self._view
        if view is None:
            # Refusal says nothing about a new source or an observed zero.
            # Retain identity for the next guarded read, but disallow input now.
            self._hold_visual(ReadingStatus.UNAVAILABLE)
            return
        if view.state not in _ACTIVE:
            self._sample = None
            self._last_visual = self._last_generation = None
            self._display = DisplayReading()
            self._view = view
            return
        changed = previous is None or (previous.revision, previous.path, previous.index, previous.continuity) != (
            view.revision, view.path, view.index, view.continuity)
        if changed:
            same_source = previous is not None and (previous.path, previous.index) == (view.path, view.index)
            pause = (same_source and view.revision == previous.revision + 1
                     and (previous.state, view.state) in _PAUSE_PAIRS)
            same_playback = (same_source and view.continuity is not None
                             and previous.continuity == view.continuity)
            if pause or same_playback:
                self._hold_visual(ReadingStatus.STALE, keep_pair=pause)
            else:
                self._last_visual = self._last_generation = None
                self._display = DisplayReading()
            self._sample = None
        self._view = view
        receipt = view.seek.receipt
        if receipt is not None and self._last_generation is not None and receipt.generation != self._last_generation:
            self._last_visual = self._last_generation = self._sample = None
            self._display = DisplayReading()
        if self._accept_terminal(view):
            return
        if self._seek_pending(view):
            return
        if view.sample is None:
            phase = view.seek.receipt.phase if view.seek.receipt is not None else None
            self._hold_visual(ReadingStatus.UNAVAILABLE, phase, keep_pair=True)
            return
        self._accept_sample(view.sample)
        if view.seek.receipt is not None:
            self._display = replace(self._display, seek_phase=view.seek.receipt.phase)

    def _accept_terminal(self, view: PlaybackView) -> bool:
        """An observed native end is not a post-seek position measurement.

        A coarse-clock/pre-SEEKED reading must remain unknown, but must not hide
        the independent current native completion fact behind a pending visual.
        """
        sample, receipt = view.sample, view.seek.receipt
        if sample is None or not sample.terminal or view.seek.blocks_end:
            return False
        self._accept_sample(sample)
        if self._sample is not sample:
            return True  # A regressive record must not promote old terminal data.
        if receipt is not None and receipt.completed_at is not None and (
                sample.clock.started_at <= receipt.completed_at
                or sample.clock.generation != receipt.generation):
            self._display = DisplayReading(status=ReadingStatus.STALE)
        self._display = replace(self._display, terminal=True, held_ratio=None,
                                seek_phase=receipt.phase if receipt is not None else None,
                                motion_mode=MotionMode.OBSERVED)
        return True

    def _seek_pending(self, view: PlaybackView) -> bool:
        """Freeze pending/error data and require a clock acquired after completion.

        Callback acknowledgement is not an observed target. Missing completion,
        expired intent or a pre-completion sample never paints a confirmed seek.
        Failed receipt phases remain exposed separately from the measured clock.
        """
        receipt = view.seek.receipt
        phase = receipt.phase if receipt is not None else None
        blocked = view.seek.blocks_end
        if receipt is not None and receipt.completed_at is not None and view.sample is not None:
            blocked = (view.sample.clock.started_at <= receipt.completed_at
                       or view.sample.clock.generation != receipt.generation)
        if blocked:
            failed = not view.seek.available or phase in (
                SeekPhase.FAILED, SeekPhase.CANCELLED, SeekPhase.EXPIRED_UNCONFIRMED,
                SeekPhase.SUBMISSION_UNCERTAIN,
            )
            status = ReadingStatus.ERROR if failed else ReadingStatus.UNAVAILABLE
            self._hold_visual(status, phase, keep_pair=True)
        return blocked

    def _accept_sample(self, sample: ProgressSnapshot) -> None:
        retained = self._sample
        if retained is not None:
            if sample.sequence < retained.sequence:
                return
            if sample.sequence == retained.sequence and (sample.stream != retained.stream
                                                         or sample.terminal != retained.terminal):
                return
            if sample.sequence > retained.sequence and sample.clock.started_at < retained.clock.started_at:
                return
            if sample.sequence == retained.sequence and sample.clock != retained.clock:
                # Expiry may remove data from a record, never replace its numbers.
                if sample.clock.position.seconds is not None or sample.clock.duration.seconds is not None:
                    return
        now = self._clock()
        reading = sample.clock
        if (finite_seconds(now) is None or now < reading.finished_at
                or (not sample.terminal and now - reading.started_at > 1.0)):
            status = ReadingStatus.STALE
        elif reading.position.status is ReadingStatus.KNOWN and reading.duration.status is ReadingStatus.KNOWN:
            if reading.duration.seconds is not None and reading.duration.seconds > 0:
                self._sample = sample
                self._display = DisplayReading(reading.position.seconds, reading.duration.seconds,
                                               ReadingStatus.KNOWN)
                return
            status = ReadingStatus.UNAVAILABLE
        else:
            status = (ReadingStatus.ERROR if ReadingStatus.ERROR in (reading.position.status, reading.duration.status)
                      else ReadingStatus.STALE if ReadingStatus.STALE in (reading.position.status, reading.duration.status)
                      else ReadingStatus.UNAVAILABLE)
        self._sample = sample
        # Retain a coherent previous PAIR only. Never combine fields from different samples.
        self._hold_visual(status, keep_pair=True)
