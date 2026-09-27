"""Bounded GUI-owned seek intent; preview is never an observed playback clock.

Source/revision changes cancel the entire gesture; duration is fixed at begin.
Invalid values, stale readings and pending native work cannot admit a request.
This module neither calls a backend nor decides end-of-stream.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
import math
from typing import TYPE_CHECKING

from src.controller.component_player.playback_state_manager import PlayerState
from src.controller.playback_view import PlaybackView
from src.playback_observation import ReadingStatus
if TYPE_CHECKING:
    from src.ui_wx.playback_presentation import DisplayReading
from src.video.seek_receipt import SeekPhase, SeekReceipt


class GesturePhase(Enum):
    IDLE = auto()
    PREVIEW = auto()
    CANCELLED = auto()
    REJECTED = auto()
    FORWARDED_UNCONFIRMED = auto()


def finite_number(value: object) -> float | None:
    """Reject invalid, overflowing or nonfinite input without truthiness coercion."""
    if type(value) not in (int, float):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def ratio_value(value: object) -> float | None:
    """Clamp an admitted scalar only at the endpoints."""
    result = finite_number(value)
    return None if result is None else min(1.0, max(0.0, result))


def eligible(view: PlaybackView | None, reading: DisplayReading) -> bool:
    """Only a coherent known pair, or a retained paused pair, can anchor a seek.

    Error/stale playing clocks refuse input. Paused clocks may be retained with no
    current sample, but failed/pending native receipts never count as permission.
    Empty sources, loading/stopped states and invalid duration always refuse.
    """
    if (view is None or view.path is None or view.seek.blocks_end
            or (view.sample is not None and view.sample.terminal)):
        return False
    active = (PlayerState.PLAYING_AUDIO, PlayerState.PLAYING_VIDEO,
              PlayerState.PAUSED_AUDIO, PlayerState.PAUSED_VIDEO)
    if view.state not in active or reading.status is ReadingStatus.ERROR:
        return False
    values = (reading.position, reading.duration)
    if any(finite_number(n) is None for n in values):
        return False
    if reading.position < 0 or reading.duration <= 0:
        return False
    if reading.status is ReadingStatus.KNOWN:
        return True
    return (view.state in (PlayerState.PAUSED_AUDIO, PlayerState.PAUSED_VIDEO)
            and view.sample is None and view.seek.receipt is None)


@dataclass(frozen=True, slots=True)
class GestureAnchor:
    """Captured semantic owner; sequence/position may advance during the gesture."""
    owner: object
    revision: int
    path: str
    index: int | None
    state: PlayerState
    is_video: bool
    duration: float
    started_at: float


class SeekGesture:
    """At most one intent. Consume-before-dispatch prevents duplicate release calls.

    No collection grows with mouse motion. Cancellation does not undo native seeks;
    a request is not sent until take_target() succeeds and the caller dispatches it.
    The GUI thread serializes this state; no native resources are retained here.
    """
    def __init__(self) -> None:
        self.anchor: GestureAnchor | None = None
        self.phase = GesturePhase.IDLE
        self.ratio: float | None = None

    @property
    def active(self) -> bool:
        return self.anchor is not None and self.phase is GesturePhase.PREVIEW

    @property
    def preview_seconds(self) -> float | None:
        anchor = self.anchor
        if not self.active or self.ratio is None or anchor is None:
            return None
        return self.ratio * anchor.duration

    def begin(self, owner: object | None, view: PlaybackView | None,
              reading: DisplayReading, now: float) -> bool:
        self.cancel()
        if (owner is None or view is None or reading.duration is None
                or not eligible(view, reading) or finite_number(now) is None or now < 0):
            self.phase = GesturePhase.REJECTED
            return False
        self.anchor = GestureAnchor(owner, view.revision, view.path, view.index,
                                    view.state, view.is_video, float(reading.duration), now)
        self.ratio = reading.ratio
        self.phase = GesturePhase.PREVIEW
        return True

    def matches(self, owner: object | None, view: PlaybackView | None,
                reading: DisplayReading, now: float) -> bool:
        anchor = self.anchor
        if (anchor is None or view is None or not self.active
                or not eligible(view, reading) or finite_number(now) is None or now < 0):
            return False
        return (owner is anchor.owner and anchor.started_at <= now <= anchor.started_at + 120.0
                and (view.revision, view.path, view.index, view.state, view.is_video,
                     reading.duration) == (anchor.revision, anchor.path, anchor.index,
                                           anchor.state, anchor.is_video, anchor.duration))

    def preview(self, value: object) -> bool:
        ratio = ratio_value(value)
        if not self.active or ratio is None:
            self.cancel()
            return False
        self.ratio = ratio
        return True

    def take_target(self, owner: object | None, view: PlaybackView | None,
                    reading: DisplayReading, now: float) -> float | None:
        if not self.matches(owner, view, reading, now):
            self.cancel()
            return None
        target = self.preview_seconds
        self.anchor = None
        self.ratio = None
        self.phase = GesturePhase.REJECTED
        return target

    def cancel(self) -> None:
        self.anchor = None
        self.ratio = None
        self.phase = GesturePhase.CANCELLED

    def mark_forwarded(self, accepted: bool) -> None:
        """This means forwarding only, never native completion or observed position."""
        self.phase = (GesturePhase.FORWARDED_UNCONFIRMED if accepted is True
                      else GesturePhase.REJECTED)


@dataclass(slots=True)
class SeekHandoff:
    """Keep one submitted thumb preview until an eligible observation replaces it.

    This is GUI drawing state, never a clock/receipt/EOS authority. Dispatch may
    reenter rendering; old/equal-time samples cannot end the handoff. Refusal,
    error, source/epoch/state drift or a ten-second absolute bound releases it.
    No timer, native call, collection growth or synchronization is introduced.
    """
    owner: object
    view: PlaybackView
    target: float
    duration: float
    started_at: float
    returned_at: float | None = None

    def _new_receipt(self, receipt: SeekReceipt | None) -> bool:
        previous = self.view.seek.receipt
        return receipt is not None and (previous is None
            or receipt.request_id != previous.request_id
            or receipt.generation != previous.generation
            or receipt.transport_epoch != previous.transport_epoch)

    def holds(self, owner: object | None, view: PlaybackView | None,
              reading: DisplayReading, now: float, *, current: bool) -> bool:
        """Retain only this request's visual intent; no deadline renewal on wakeups."""
        if (view is None or owner is not self.owner or finite_number(now) is None
                or not self.started_at <= now < self.started_at + 10.0
                or not view.seek.available or reading.status is ReadingStatus.ERROR
                or reading.terminal):
            return False
        old = self.view
        if (view.path != old.path or view.index != old.index or view.state is not old.state
                or view.is_video != old.is_video or view.continuity != old.continuity
                or (view.revision != old.revision and view.revision != old.revision + 1)
                or (reading.status is ReadingStatus.KNOWN and reading.duration != self.duration)):
            return False
        sample, prior = view.sample, old.sample
        if sample is not None and prior is not None and (
                sample.clock.generation != prior.clock.generation
                or sample.stream != prior.stream):
            return False
        receipt = view.seek.receipt
        if receipt is not None and self._new_receipt(receipt):
            if (receipt.target != self.target or receipt.created_at < self.started_at
                    or receipt.phase in (SeekPhase.REJECTED, SeekPhase.FAILED,
                        SeekPhase.CANCELLED, SeekPhase.EXPIRED_UNCONFIRMED,
                        SeekPhase.SUBMISSION_UNCERTAIN)
                    or (prior is not None and receipt.generation != prior.clock.generation)):
                return False
        return not self._observed(view, reading, current)

    def _observed(self, view: PlaybackView, reading: DisplayReading, current: bool) -> bool:
        """A newer observed clock wins even when it differs from the chosen target."""
        sample, returned = view.sample, self.returned_at
        if (returned is None or sample is None or not current or view.seek.blocks_end
                or reading.status is not ReadingStatus.KNOWN
                or sample.clock.started_at <= returned):
            return False
        if view.seek.supported:
            receipt = view.seek.receipt
            if (receipt is None or not self._new_receipt(receipt)
                    or receipt.phase is not SeekPhase.NATIVE_COMPLETED
                    or receipt.completed_at is None
                    or sample.clock.started_at <= receipt.completed_at
                    or sample.clock.generation != receipt.generation):
                return False
        return True
