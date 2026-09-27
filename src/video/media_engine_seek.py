"""Explicit asynchronous seek admission and native-call results.

Cold bounded control path: one outstanding command per core. The SDK call is
executed on the existing COM worker with the admitted engine held locally. This
module records call facts only; seek_receipt and generation-bound notifications
correlate completion separately. No requested target is a measured position.
"""
from __future__ import annotations

import ctypes
from typing import Callable, Protocol

from .media_engine_clock import ClockCore, _identity, _held_engine, _Identity
from .media_engine_core_shared import ComCallAdapter, QUERY_EXCEPTIONS
from .seek_receipt import NativeSeekResult, SeekOperation, SeekPhase, SeekReceipt, SeekSlot, seek_seconds


class SeekAdapter(ComCallAdapter, Protocol):
    def submit_to_com_thread(self, name: str, callback: Callable[[], object], response: SeekOperation) -> bool: ...


class SeekCore(ClockCore, Protocol):
    _seek_slot: SeekSlot

    def _adapter_ref(self) -> SeekAdapter | None: ...
    def _call_engine_ptr_method(self, engine: object, name: str, *args: object) -> object: ...


def invalidate_seek(core: SeekCore, reason: str) -> int:
    """Fail closed if the canonical core-owned cancellation state is absent."""
    pipeline = getattr(core, '_wic_pipeline', None)
    if pipeline is not None:
        pipeline.invalidate()
    slot = core._seek_slot
    if type(slot) is not SeekSlot:
        raise TypeError('Core has no valid seek ownership slot')
    return slot.invalidate(reason)


def read_seek_receipt(core: SeekCore) -> SeekReceipt | None:
    """Read current command facts, not a clock or invented completion.

    A successful source replacement retires only drained earlier commands. A
    failed load, stop, malformed identity or unreplied worker remains explicit;
    the original operation retains all cancellation/native facts for diagnostics.
    """
    slot = core._seek_slot
    operation = slot.current()
    if operation is None:
        return None
    value = operation.snapshot()
    epoch = slot.epoch
    try:
        current = _identity(core)
        valid = (current.readable and current.generation == value.generation
                 and current.active == value.source and epoch == value.transport_epoch)
    except QUERY_EXCEPTIONS + (ctypes.ArgumentError,):
        current, valid = None, False
    if (current is not None and current.readable
            and slot.supersedes(value, current.generation, current.active, epoch)):
        if (slot.current() is not operation or slot.epoch != epoch
                or _identity(core).key != current.key):
            raise RuntimeError('Source ownership changed while retiring seek observation')
        return None
    if not valid:
        operation.cancel('Cached command belongs to invalidated source/engine context')
        value = operation.snapshot()
    return value


def _is_current(core: SeekCore, adapter: SeekAdapter, expected: _Identity, epoch: int) -> bool:
    return (core._seek_slot.epoch == epoch and core._adapter_ref() is adapter
            and _identity(core).key == expected.key)


def _execute(core: SeekCore, adapter: SeekAdapter, expected: _Identity,
             epoch: int, seconds: float, operation: SeekOperation) -> NativeSeekResult:
    """Guard queued ownership, retain, call, inspect HRESULT and release on every path."""
    if expected.address is None or not _is_current(core, adapter, expected, epoch):
        return NativeSeekResult(SeekPhase.CANCELLED, detail='Source or engine changed before dispatch')
    phase = operation.snapshot().phase
    if phase not in (SeekPhase.RESERVED, SeekPhase.QUEUED):
        return NativeSeekResult(phase if phase is SeekPhase.EXPIRED_UNCONFIRMED else SeekPhase.CANCELLED)
    with _held_engine(expected.address) as engine:
        if not _is_current(core, adapter, expected, epoch):
            return NativeSeekResult(SeekPhase.CANCELLED, detail='Source or engine changed during retention')
        if not operation.begin_native():
            return NativeSeekResult(SeekPhase.CANCELLED, detail='Command invalidated before SetCurrentTime')
        raw = core._call_engine_ptr_method(engine, 'SetCurrentTime', ctypes.c_double(seconds))
        if type(raw) is not int or not -(1 << 31) <= raw <= 0xFFFFFFFF:
            return NativeSeekResult(SeekPhase.FAILED, detail='Malformed SetCurrentTime HRESULT')
        hr = raw & 0xFFFFFFFF
        if not _is_current(core, adapter, expected, epoch):
            return NativeSeekResult(SeekPhase.CANCELLED, hr, 'Source/engine changed during native call')
        if hr != 0:
            return NativeSeekResult(SeekPhase.FAILED, hr, f'SetCurrentTime returned 0x{hr:08X}')
        return NativeSeekResult(SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED, hr)


def submit_seek(core: SeekCore, seconds: object) -> bool:
    """Return True only for exact queue admission; later failure is in the receipt.

    No implicit worker startup, scalar/native fallback or retry. Reject malformed
    targets, missing source, busy slot and queue refusal before a native request.
    Engine/source changes and expiry after admission remain explicit uncertainty.
    """
    target = seek_seconds(seconds)
    if target is None:
        return False
    operation = None
    try:
        before = _identity(core)
        adapter = core._adapter_ref()
        slot = core._seek_slot
        epoch = slot.epoch
        if not before.readable or adapter is None:
            return False
        operation = slot.reserve(before.active, before.generation, epoch, target)
        if operation is None:
            return False
        if not _is_current(core, adapter, before, epoch):
            operation.refuse('Context changed before queue admission')
            return False
        pipeline = getattr(core, '_wic_pipeline', None)
        if pipeline is not None:
            pipeline.invalidate()
        accepted = adapter.submit_to_com_thread(
            'seek', lambda: _execute(core, adapter, before, epoch, target, operation), operation)
        if accepted is not True:
            operation.refuse('COM worker refused queue admission')
            return False
        operation.mark_queued()
        return True
    except QUERY_EXCEPTIONS + (ctypes.ArgumentError,) as error:
        if operation is not None:
            operation.submission_uncertain(error)
        return False
