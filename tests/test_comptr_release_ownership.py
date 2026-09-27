"""ComPtr ownership regressions; allocated IUnknown-shaped storage is NOT native COM.

The receiver never frees memory or raises through a ctypes callback. Reentry,
overlap, separately acquired aliases and rejected transfers are tested explicitly.
Borrowed views and apartment validity remain caller responsibilities.
"""
from __future__ import annotations

import ctypes
import threading

import pytest

from src.video.component_base.com_helpers import ComPtr
from src.video.component_base.definitions import IUnknown, IUnknownVtbl
from src.video.component_base.utils import safe_release


class Receiver:
    """Independent reference ledger with storage kept alive for the entire test."""
    def __init__(self, refs=1):
        self.refs = refs
        self.calls = []
        self.errors = []
        self.hook = None
        self.lock = threading.RLock()
        types = dict(IUnknownVtbl._fields_)
        self.callbacks = (
            types["QueryInterface"](lambda *args: -2147467262),
            types["AddRef"](self.addref),
            types["Release"](self.release),
        )
        self.vtable = IUnknownVtbl(*self.callbacks)
        self.storage = IUnknown(ctypes.pointer(self.vtable))
        self.ptr = ctypes.pointer(self.storage)
        self.address = ctypes.cast(self.ptr, ctypes.c_void_p).value

    def addref(self, _this):
        with self.lock:
            self.refs += 1
            return self.refs

    def release(self, _this):
        with self.lock:
            self.refs -= 1
            after = self.refs
            self.calls.append(after)
            ordinal = len(self.calls)
        if self.hook is not None:
            self.hook(ordinal)
        return max(0, after)


@pytest.mark.parametrize("via_safe", [False, True])
def test_reentry_cannot_claim_the_owned_reference_twice(via_safe):
    receiver = Receiver(2)
    owner = ComPtr(receiver.ptr)
    observations = []
    def invoke():
        return safe_release(owner) if via_safe else owner.release()
    def hook(ordinal):
        if ordinal == 1:
            observations.append((owner.ptr, bool(owner)))
            invoke()
    receiver.hook = hook
    try:
        invoke()
        assert receiver.calls == [1] and receiver.refs == 1
        assert observations == [(None, False)]
    finally:
        receiver.hook = None
        owner.detach()


@pytest.mark.parametrize("via_safe", [False, True])
def test_overlap_cannot_claim_the_owned_reference_twice(via_safe):
    receiver = Receiver(2)
    owner = ComPtr(receiver.ptr)
    entered, resume = threading.Event(), threading.Event()
    def hook(ordinal):
        if ordinal == 1:
            entered.set()
            if not resume.wait(3):
                receiver.errors.append("release gate expired")
    def invoke():
        return safe_release(owner) if via_safe else owner.release()
    receiver.hook = hook
    first = threading.Thread(target=invoke)
    first.start()
    try:
        assert entered.wait(2)
        invoke()
        assert owner.ptr is None
    finally:
        resume.set()
        first.join(4)
        receiver.hook = None
        owner.detach()
    assert not first.is_alive() and not receiver.errors
    assert receiver.calls == [1] and receiver.refs == 1


@pytest.mark.parametrize("kind", ["typed", "integer", "void"])
def test_one_acquisition_released_once_sequentially(kind):
    receiver = Receiver(2)
    value = {"typed": receiver.ptr, "integer": receiver.address,
             "void": ctypes.c_void_p(receiver.address)}[kind]
    owner = ComPtr(value)
    try:
        assert owner.release() == 1
        first = owner.release_snapshot
        assert owner.release() == 0
        assert owner.release_snapshot == first
        assert first.state.value == "released" and first.refcount == 1
        assert first.address == receiver.address and first.error is None
        assert receiver.calls == [1]
    finally:
        owner.detach()


@pytest.mark.parametrize("method", ["attach", "adopt"])
@pytest.mark.parametrize("same_address", [False, True])
def test_fresh_transfer_during_release_survives_completion(method, same_address):
    old = Receiver(2 if same_address else 1)
    new = old if same_address else Receiver()
    owner = ComPtr(old.ptr)
    def hook(ordinal):
        if ordinal == 1:
            getattr(owner, method)(new.ptr)
    old.hook = hook
    try:
        owner.release()
        assert owner.ptr.value == new.address
        assert new.refs == 1
        old.hook = None
        owner.release()
        assert new.refs == 0
        assert len(old.calls) == (2 if same_address else 1)
    finally:
        old.hook = None
        owner.detach()


def test_same_address_attach_noop_and_adopt_new_reference():
    receiver = Receiver()
    owner = ComPtr(receiver.ptr)
    try:
        initial = owner._gen
        owner.attach(owner.ptr)
        assert not receiver.calls and owner._gen == initial
        assert owner.add_ref() == 2  # An actual independent test-ledger acquisition.
        owner.adopt(receiver.ptr)
        assert receiver.calls == [1] and owner.ptr.value == receiver.address
        owner.release()
        assert receiver.calls == [1, 0]
    finally:
        owner.detach()


def test_same_numeric_address_reused_by_a_new_comptr_ownership():
    receiver = Receiver()
    owner = ComPtr(receiver.ptr)
    try:
        assert owner.release() == 0
        receiver.refs = 1  # New logical lifetime; test storage is never freed.
        owner.adopt(receiver.ptr)
        owner.release()
        assert receiver.calls == [0, 0]
        assert receiver.refs == 0
    finally:
        owner.detach()


def test_distinct_comptr_owners_of_equal_address_keep_both_references():
    receiver = Receiver(2)
    first, second = ComPtr(receiver.ptr), ComPtr(receiver.ptr)
    try:
        first.release()
        assert receiver.refs == 1 and bool(second)
        second.release()
        assert receiver.refs == 0 and receiver.calls == [1, 0]
    finally:
        first.detach()
        second.detach()


def test_detach_transfers_current_reference_without_release():
    receiver = Receiver()
    owner = ComPtr(receiver.ptr)
    raw = owner.detach()
    assert raw.value == receiver.address and not owner and not receiver.calls
    recipient = ComPtr(raw)
    recipient.release()
    assert receiver.refs == 0


def test_foreign_release_does_not_hold_wrapper_lock():
    receiver = Receiver()
    owner = ComPtr(receiver.ptr)
    observations = []
    def hook(_ordinal):
        def inspect():
            acquired = owner._lock.acquire(timeout=1)
            observations.append(acquired)
            if acquired:
                owner._lock.release()
        thread = threading.Thread(target=inspect)
        thread.start()
        thread.join(2)
        if thread.is_alive():
            receiver.errors.append("inspection blocked")
    receiver.hook = hook
    try:
        owner.release()
        assert observations == [True] and not receiver.errors
    finally:
        receiver.hook = None
        owner.detach()


@pytest.mark.parametrize("method", ["attach", "adopt"])
def test_replacement_is_atomic_and_second_transfer_is_rejected(method):
    old, current, incoming = Receiver(), Receiver(), Receiver()
    owner = ComPtr(old.ptr)
    entered, resume = threading.Event(), threading.Event()
    failures = []
    def hook(_ordinal):
        entered.set()
        if not resume.wait(3):
            old.errors.append("release gate expired")
    def replace():
        try:
            getattr(owner, method)(current.ptr)
        except RuntimeError as error:
            failures.append(error)
    old.hook = hook
    worker = threading.Thread(target=replace)
    worker.start()
    try:
        assert entered.wait(2)
        assert owner.ptr.value == current.address
        with pytest.raises(RuntimeError, match="already in progress"):
            getattr(owner, method)(incoming.ptr)
        assert owner.ptr.value == current.address and not incoming.calls
        with pytest.raises(RuntimeError, match="already in progress"):
            owner.release()
    finally:
        resume.set()
        worker.join(4)
        old.hook = None
    try:
        assert not worker.is_alive() and not failures and not old.errors
        owner.release()
        assert old.calls == [0] and current.calls == [0] and not incoming.calls
    finally:
        owner.detach()
        # The rejected incoming acquisition still belongs to its original sender.
        ComPtr(incoming.ptr).release()


def test_borrowed_views_during_claimed_empty_slot_are_empty():
    receiver = Receiver()
    owner = ComPtr(receiver.ptr)
    observations = []
    def hook(_ordinal):
        observations.append((owner.ptr, owner.as_iunknown(), owner.add_ref()))
    receiver.hook = hook
    try:
        owner.release()
        assert observations == [(None, None, 0)] and receiver.calls == [0]
    finally:
        receiver.hook = None
        owner.detach()


def test_many_callers_share_one_claim_not_one_release_each():
    receiver = Receiver(2)
    owner = ComPtr(receiver.ptr)
    barrier = threading.Barrier(9)
    entered, resume, duplicates_done = threading.Event(), threading.Event(), threading.Event()
    results, failures = [], []
    result_lock = threading.Lock()
    def hook(_ordinal):
        entered.set()
        if not resume.wait(4):
            receiver.errors.append("release gate expired")
    def invoke():
        barrier.wait(timeout=3)
        try:
            result = owner.release()
            with result_lock:
                results.append(result)
                if len(results) == 7:
                    duplicates_done.set()
        except RuntimeError as error:
            failures.append(error)
    receiver.hook = hook
    threads = [threading.Thread(target=invoke) for _ in range(8)]
    for thread in threads:
        thread.start()
    try:
        barrier.wait(timeout=3)
        assert entered.wait(2) and duplicates_done.wait(3)
    finally:
        resume.set()
        for thread in threads:
            thread.join(5)
        receiver.hook = None
        owner.detach()
    assert not any(thread.is_alive() for thread in threads)
    assert not failures and not receiver.errors
    assert sorted(results) == [0] * 7 + [1]
    assert receiver.calls == [1] and receiver.refs == 1


def test_snapshot_is_immutable_and_previous_capture_does_not_change():
    receiver = Receiver()
    owner = ComPtr(receiver.ptr)
    before = []
    receiver.hook = lambda _ordinal: before.append(getattr(owner, "release_snapshot", None))
    try:
        owner.release()
        assert before[0].state.value == "dispatched"
        assert owner.release_snapshot.state.value == "released"
        with pytest.raises(AttributeError):
            before[0].address = 0
    finally:
        receiver.hook = None
        owner.detach()
