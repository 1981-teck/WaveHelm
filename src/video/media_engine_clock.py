"""Strict, paired Media Engine clock acquisition on its existing COM thread.

Missing sources, replacement during dispatch, invalid native values and query
failures are explicit nonnumeric readings. No scalar getter's zero fallback is
used. Before/after seals detect observed drift, not every possible ABA race.
This cold path is O(1), with bounded state and no native call under a state lock.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import ctypes
import time
from typing import Protocol, Iterator

from src.playback_observation import (
    ClockObservation, ClockOrigin, ClockValue, ReadingStatus, empty_clock, valid_source,
)
from .media_engine_core_shared import CoreState, QUERY_EXCEPTIONS
from .component_base.definitions_abi import IMFMediaEngine, IUnknown


class ClockCore(CoreState, Protocol):
    """Only the core ownership and raw native invocation needed by this query."""

    _engine_generation: int
    _source: str | None
    _requested_source: str | None
    _active_source: str | None

    def _call_engine_ptr_method(self, engine: object, name: str) -> object: ...


@dataclass(frozen=True, slots=True)
class _Identity:
    engine: object | None
    generation: int
    address: int | None
    requested: str | None
    active: str | None
    source: str | None
    shutdown: bool

    @property
    def key(self) -> tuple[int, int, int | None, str | None, str | None, str | None, bool]:
        return (id(self.engine), self.generation, self.address, self.requested, self.active, self.source, self.shutdown)

    @property
    def readable(self) -> bool:
        return self.address is not None and not self.shutdown and self.active is not None and (
            self.requested == self.active == self.source
        )


def _identity(core: ClockCore) -> _Identity:
    """Capture a small ownership record; validation and comparison occur unlocked."""
    with core._state_lock:
        engine = core._media_engine
        generation, requested = core._engine_generation, core._requested_source
        active, source, shutdown = core._active_source, core._source, core._shutdown_requested
    if engine is not None and not isinstance(engine, ctypes.POINTER(IMFMediaEngine)):
        raise TypeError("Expected a typed Media Engine interface pointer")
    address = ctypes.cast(engine, ctypes.c_void_p).value if engine is not None else None
    result = _Identity(engine, generation, address, requested, active, source, shutdown)
    if type(result.generation) is not int or result.generation < 0 or type(result.shutdown) is not bool:
        raise ValueError("Invalid Media Engine clock ownership")
    if not all(valid_source(path) for path in (result.requested, result.active, result.source)):
        raise ValueError("Invalid Media Engine clock source")
    return result


@contextmanager
def _held_engine(address: int) -> Iterator[object]:
    """Retain one COM reference throughout the paired read, release exactly once.

    The validated address belongs to the core on its serialized COM thread.
    Retention protects against reentrant teardown after a getter. NULL methods
    fail before AddRef; an exception on Release invalidates the whole result.
    AddRef/Release counts are diagnostic values, not HRESULT success codes.
    """
    owned = ctypes.cast(address, ctypes.POINTER(IMFMediaEngine))
    unknown = ctypes.cast(address, ctypes.POINTER(IUnknown))
    if not unknown.contents.lpVtbl:
        raise ValueError("Media Engine has a NULL IUnknown vtable")
    add_ref = unknown.contents.lpVtbl.contents.AddRef
    release = unknown.contents.lpVtbl.contents.Release
    if not add_ref or not release:
        raise ValueError("Media Engine is missing AddRef/Release")
    add_ref(ctypes.c_void_p(address))
    try:
        yield owned
    finally:
        release(ctypes.c_void_p(address))


def _read_on_com_thread(core: ClockCore, before: _Identity, started: float) -> ClockObservation:
    """Read both native fields on one task, refusing replaced/closing ownership."""
    if _identity(core).key != before.key:
        return empty_clock(ClockOrigin.VIDEO_NATIVE, started, time.monotonic(), status=ReadingStatus.STALE)
    with _held_engine(before.address) as engine:
        if _identity(core).key != before.key:
            return empty_clock(ClockOrigin.VIDEO_NATIVE, started, time.monotonic(), status=ReadingStatus.STALE)
        position = ClockValue.read(core._call_engine_ptr_method(engine, "GetCurrentTime"))
        if _identity(core).key != before.key:
            return empty_clock(ClockOrigin.VIDEO_NATIVE, started, time.monotonic(), status=ReadingStatus.STALE)
        duration = ClockValue.read(core._call_engine_ptr_method(engine, "GetDuration"), duration=True)
        result = ClockObservation(position, duration, started, time.monotonic(),
                                  ClockOrigin.VIDEO_NATIVE, before.active, before.generation)
        return result if _identity(core).key == before.key else result.invalidate()


def read_video_clock(core: ClockCore) -> ClockObservation:
    """Dispatch one paired read; exceptions, absence and drift cannot look like zero.

    Successful data does not imply seek completion, no buffering, or frame output.
    A queued seek still needs the later native acknowledgement repair.
    """
    started = time.monotonic()
    try:
        before = _identity(core)
        if not before.readable:
            return empty_clock(ClockOrigin.VIDEO_NATIVE, started, time.monotonic())
        adapter = core._adapter_ref()
        if adapter is None:
            return empty_clock(ClockOrigin.VIDEO_NATIVE, started, time.monotonic())
        result = adapter.call_on_com_thread(
            "observe_progress", lambda: _read_on_com_thread(core, before, started),
        )
        if type(result) is not ClockObservation:
            raise TypeError("COM dispatcher returned an invalid clock observation")
        if _identity(core).key != before.key or core._adapter_ref() is not adapter:
            return result.invalidate()
        return result
    except QUERY_EXCEPTIONS + (ctypes.ArgumentError,) as error:
        return empty_clock(ClockOrigin.VIDEO_NATIVE, started, time.monotonic(), error=error)
