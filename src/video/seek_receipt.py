"""Bounded seek transport and serialized native completion receipts.

One core retains one operation. Cancellation/expiry do not pretend to undo native
work; a native operation retains its slot beyond the worker reply until SEEKED.
Error text is bounded at the response boundary. No callback or I/O runs under locks.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto
import math
import queue
import threading
import time


class SeekPhase(Enum):
    """HRESULT acceptance and callback-observed completion are distinct facts."""

    RESERVED = auto()
    QUEUED = auto()
    SUBMISSION_UNCERTAIN = auto()
    RUNNING = auto()
    NATIVE_ACCEPTED_UNCONFIRMED = auto()
    NATIVE_COMPLETED = auto()
    REJECTED = auto()
    FAILED = auto()
    CANCELLED = auto()
    EXPIRED_UNCONFIRMED = auto()


def seek_seconds(value: object) -> float | None:
    """Reject bool, overflow, NaN and infinity; preserve finite-negative clamping."""
    if type(value) not in (int, float):
        return None
    try:
        result = float(value)
    except OverflowError:
        return None
    return max(0.0, result) if math.isfinite(result) else None


@dataclass(frozen=True, slots=True)
class SeekReceipt:
    """Pointer-free request facts; native seek acknowledgement is not output playback."""

    request_id: int
    source: str
    generation: int
    transport_epoch: int
    target: float
    phase: SeekPhase
    created_at: float
    deadline: float
    queued: bool = False
    native_call_started: bool = False
    worker_replied: bool = False
    hresult: int | None = None
    error_type: str = ''
    detail: str = ''
    worker_phase: SeekPhase | None = None
    worker_error_type: str = ''
    worker_detail: str = ''
    seeking_at: float | None = None
    seeked_at: float | None = None
    completed_at: float | None = None
    worker_replied_at: float | None = None

    def __post_init__(self) -> None:
        for value in (self.request_id, self.generation, self.transport_epoch):
            if type(value) is not int or value < 0:
                raise ValueError('Invalid seek identity')
        if type(self.source) is not str or not self.source or len(self.source) > 32768 or '\0' in self.source:
            raise ValueError('Invalid seek source')
        for value in (self.target, self.created_at, self.deadline):
            if type(value) is not float or not math.isfinite(value) or value < 0:
                raise ValueError('Invalid seek numeric field')
        if self.deadline <= self.created_at or type(self.phase) is not SeekPhase:
            raise ValueError('Invalid seek phase/deadline')
        for value in (self.queued, self.native_call_started, self.worker_replied):
            if type(value) is not bool:
                raise ValueError('Invalid seek evidence flag')
        if self.worker_phase is not None and type(self.worker_phase) is not SeekPhase:
            raise ValueError('Invalid worker outcome phase')
        for value in (self.error_type, self.detail, self.worker_error_type, self.worker_detail):
            if type(value) is not str or len(value) > 256:
                raise ValueError('Invalid seek error text')
        if self.hresult is not None and (type(self.hresult) is not int or not 0 <= self.hresult <= 0xFFFFFFFF):
            raise ValueError('Invalid seek HRESULT')
        for stamp in (self.seeking_at, self.seeked_at, self.completed_at, self.worker_replied_at):
            if stamp is not None and (type(stamp) is not float or not math.isfinite(stamp)
                                      or stamp < self.created_at):
                raise ValueError('Invalid seek event timestamp')
        if self.seeked_at is not None and (self.seeking_at is None or self.seeked_at < self.seeking_at):
            raise ValueError('SEEKED requires preceding SEEKING')
        if self.completed_at is not None and (self.seeked_at is None or self.completed_at < self.seeked_at
                                              or self.worker_replied_at is None or self.completed_at < self.worker_replied_at
                                              or not self.worker_replied or not self.native_call_started
                                              or self.hresult != 0 or self.completed_at >= self.deadline
                                              or self.worker_phase is not SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED):
            raise ValueError('Completion requires event and worker facts')
        if self.phase is SeekPhase.NATIVE_COMPLETED and self.completed_at is None:
            raise ValueError('Completed phase requires completion timestamp')
        if self.phase is SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED and self.hresult != 0:
            raise ValueError('Native acceptance requires S_OK')

    @property
    def blocks_end(self) -> bool:
        """Unknown native side effects remain unresolved, even after a timeout."""
        if self.phase is SeekPhase.REJECTED or self.completed_at is not None:
            return False
        if not self.worker_replied:
            return True
        if not self.native_call_started:
            return False
        # A known failing HRESULT did not accept a seek. A lost/invalid reply
        # cannot establish that no asynchronous native side effect exists.
        return not (self.worker_phase is SeekPhase.FAILED and self.hresult not in (None, 0))

    def __bool__(self) -> bool:
        raise TypeError('Inspect seek phase explicitly; a receipt is not seek completion')


@dataclass(frozen=True, slots=True)
class NativeSeekResult:
    """Return-only command evidence supplied by the COM worker."""

    phase: SeekPhase
    hresult: int | None = None
    detail: str = ''

    def __post_init__(self) -> None:
        if self.phase not in (SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED, SeekPhase.FAILED,
                              SeekPhase.CANCELLED, SeekPhase.EXPIRED_UNCONFIRMED):
            raise ValueError('Invalid worker phase')
        if self.hresult is not None and (type(self.hresult) is not int or not 0 <= self.hresult <= 0xFFFFFFFF):
            raise ValueError('Invalid HRESULT')
        if self.phase is SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED and self.hresult != 0:
            raise ValueError('Native acceptance requires S_OK')
        if type(self.detail) is not str or len(self.detail) > 256:
            raise ValueError('Invalid native detail')


class SeekOperation:
    """One nonblocking reply port compatible with the existing COM worker protocol."""

    def __init__(self, record: SeekReceipt) -> None:
        self._lock = threading.Lock()
        self._record = record

    def snapshot(self, now: float | None = None) -> SeekReceipt:
        """Read/expire without COM, waits, polling loops or invented success."""
        current = time.monotonic() if now is None else now
        if type(current) not in (int, float) or not math.isfinite(current) or current < 0:
            raise ValueError('Invalid receipt observation time')
        with self._lock:
            record = self._record
            if current >= record.deadline and record.phase in (SeekPhase.RESERVED, SeekPhase.QUEUED, SeekPhase.RUNNING,
                                                                            SeekPhase.SUBMISSION_UNCERTAIN,
                                                                            SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED):
                record = replace(record, phase=SeekPhase.EXPIRED_UNCONFIRMED,
                                 detail='Command deadline elapsed; native completion is unknown')
                self._record = record
            return record

    def mark_queued(self) -> None:
        """Queue admission can be recorded after a very fast worker reply."""
        with self._lock:
            record = self._record
            phase = SeekPhase.QUEUED if record.phase is SeekPhase.RESERVED else record.phase
            self._record = replace(record, queued=True, phase=phase)

    def refuse(self, detail: str) -> None:
        """Only use when submission was actually refused before insertion."""
        message = detail[:256]
        with self._lock:
            if self._record.queued or self._record.native_call_started or self._record.worker_replied:
                raise ValueError('Cannot claim refusal after command evidence')
            self._record = replace(self._record, phase=SeekPhase.REJECTED, detail=message)

    def submission_uncertain(self, error: Exception) -> None:
        """A dispatcher exception cannot prove that no command was inserted."""
        kind, detail = type(error).__name__[:256], str(error)[:256]
        with self._lock:
            if not self._record.worker_replied:
                self._record = replace(self._record, phase=SeekPhase.SUBMISSION_UNCERTAIN,
                                       error_type=kind, detail=detail)

    def cancel(self, detail: str) -> None:
        """Invalidate intent, not the native side effect or an outstanding queue item."""
        message = detail[:256]
        with self._lock:
            if self._record.phase is not SeekPhase.REJECTED:
                self._record = replace(self._record, phase=SeekPhase.CANCELLED, detail=message)

    def begin_native(self) -> bool:
        """Refuse cancelled, expired or duplicate work before dereferencing COM."""
        current = time.monotonic()
        self.snapshot(current)
        with self._lock:
            if self._record.phase not in (SeekPhase.RESERVED, SeekPhase.QUEUED):
                return False
            self._record = replace(self._record, phase=SeekPhase.RUNNING, queued=True, native_call_started=True)
            return True

    def observe_event(self, event: int, generation: int, epoch: int, source: str) -> bool:
        """Admit an ordered callback pair without COM calls or a fabricated position.

        Unstarted, wrong-owner, duplicate and expired events cannot confirm intent.
        The native API has no request token: attribution requires serialized seeks
        and a generation-bound callback. Missing callbacks remain unconfirmed.
        """
        if type(event) is not int or type(generation) is not int or type(epoch) is not int or type(source) is not str:
            return False
        now = time.monotonic()
        self.snapshot(now)
        with self._lock:
            old = self._record
            if (old.generation, old.transport_epoch, old.source) != (generation, epoch, source):
                return False
            if old.phase not in (SeekPhase.RUNNING, SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED):
                return False
            if not old.native_call_started or now >= old.deadline:
                return False
            if event == 16 and old.seeking_at is None:
                self._record = replace(old, seeking_at=now)
            elif event == 17 and old.seeking_at is not None and old.seeked_at is None:
                self._record = replace(old, seeked_at=now)
            else:
                return False
            self._complete_locked(now)
        return True

    def _complete_locked(self, now: float) -> None:
        """Reconcile a fast callback before worker return; caller owns the lock."""
        old = self._record
        if (old.phase is SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED and old.worker_replied
                and old.hresult == 0 and old.seeked_at is not None and old.worker_replied_at is not None):
            # A callback can merge after the worker sampled its time, or vice versa.
            # Preserve both observations; never stamp completion before either fact.
            completed = max(now, old.seeked_at, old.worker_replied_at)
            if completed < old.deadline:
                self._record = replace(old, phase=SeekPhase.NATIVE_COMPLETED, completed_at=completed)

    def put_nowait(self, result: object) -> None:
        """Worker/queue-rejection boundary; retain scalar evidence, never exceptions.

        Duplicate replies are rejected. Errors, cancellation and late success do
        not become seek completion. The port stores at most one immutable record.
        """
        if isinstance(result, Exception):
            phase, hr = SeekPhase.FAILED, None
            kind, detail = type(result).__name__[:256], str(result)[:256]
        elif type(result) is NativeSeekResult:
            phase, hr = result.phase, result.hresult
            kind, detail = '', result.detail
        else:
            phase, hr = SeekPhase.FAILED, None
            kind, detail = 'TypeError', 'COM worker supplied an invalid seek result'
        current = time.monotonic()
        self.snapshot(current)
        with self._lock:
            old = self._record
            if old.worker_replied:
                raise queue.Full('Seek operation already has a worker reply')
            if phase is SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED and not old.native_call_started:
                phase, kind, detail = SeekPhase.FAILED, 'TypeError', 'Acceptance without a recorded native call'
            worker_phase, worker_kind, worker_detail = phase, kind, detail
            if old.phase in (SeekPhase.CANCELLED, SeekPhase.EXPIRED_UNCONFIRMED):
                phase, kind, detail = old.phase, old.error_type, old.detail
            self._record = replace(old, phase=phase, hresult=hr, error_type=kind,
                                   detail=detail, worker_replied=True, worker_phase=worker_phase,
                                   worker_error_type=worker_kind, worker_detail=worker_detail,
                                   worker_replied_at=current)
            self._complete_locked(current)


class SeekSlot:
    """Bounded core-owned operation/epoch. An unreplied item cannot be replaced."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._epoch = 0
        self._sequence = 0
        self._current: SeekOperation | None = None
        self._source_commit: tuple[int, int, str] | None = None

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    def current(self) -> SeekOperation | None:
        with self._lock:
            return self._current

    def reserve(self, source: str, generation: int, epoch: int, target: float) -> SeekOperation | None:
        """Admit one request; reject concurrent reservations rather than retry."""
        previous = self.current()
        if previous is not None:
            prior = previous.snapshot()
            if prior.blocks_end and not (prior.worker_replied and prior.generation != generation
                                         or self.supersedes(prior, generation, source, epoch)):
                return None
        now = time.monotonic()
        with self._lock:
            if self._current is not previous or self._epoch != epoch:
                return None
            self._sequence += 1
            sequence = self._sequence
        record = SeekReceipt(sequence, source, generation, epoch, target,
                             SeekPhase.RESERVED, now, now + 10.0)
        operation = SeekOperation(record)
        with self._lock:
            if self._current is not previous or self._epoch != epoch or self._sequence != sequence:
                return None
            self._current = operation
        return operation

    def invalidate(self, detail: str) -> int:
        """Stop/reload invalidate even a same-path engine; no I/O under this lock."""
        with self._lock:
            self._epoch += 1
            epoch = self._epoch
            current = self._current
        if current is not None:
            current.cancel(detail)
        return epoch

    def commit_source(self, epoch: int, generation: int, source: str) -> bool:
        """Seal one successful non-NULL SetSource, never stop or mere invalidation.

        The COM owner calls this only after S_OK; asynchronous loading is not
        claimed complete. Stale epochs,
        duplicate commits and malformed ownership cannot retire an old operation.
        Validation/string work is outside the lock; retained state is O(1).
        """
        if (any(type(value) is not int or value < 0 for value in (epoch, generation))
                or type(source) is not str or not source or len(source) > 32768
                or '\0' in source):
            raise ValueError('Invalid source commit identity')
        committed = (epoch, generation, source)
        with self._lock:
            if self._epoch != epoch or (self._source_commit is not None
                                        and self._source_commit[0] == epoch):
                return False
            self._source_commit = committed
        return True

    def supersedes(self, record: SeekReceipt, generation: int, source: str,
                   expected_epoch: int) -> bool:
        """A drained older operation cannot govern a committed replacement source.

        Same-path replay needs a newer committed epoch too. Unreplied work stays
        reserved unless explicitly refused before insertion; stop is not a commit. Historical
        facts remain in the operation until the next bounded-slot reservation.
        """
        with self._lock:
            epoch, committed = self._epoch, self._source_commit
        drained = (record.worker_replied or (record.phase is SeekPhase.REJECTED
                   and not record.queued and not record.native_call_started))
        return (epoch == expected_epoch and drained and record.transport_epoch < epoch
                and record.generation <= generation
                and committed == (epoch, generation, source))
