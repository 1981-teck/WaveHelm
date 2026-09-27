"""Failure disposition without retrying ambiguous foreign effects.

Use a Python call-boundary recorder, never raise across a ctypes callback.
Pre-dispatch errors, post-dispatch ambiguity, rejected transfer and deferred GC
are separate contracts. These are not Windows COM or allocator tests.
"""
from __future__ import annotations

import ctypes
import gc
from types import SimpleNamespace

import pytest

import src.video.component_base.com_helpers as ch
from src.video.component_base.utils import safe_release
from tests.test_comptr_release_ownership import Receiver, IUnknownVtbl


ERRORS = [TypeError, ValueError, RuntimeError, ctypes.ArgumentError,
          KeyError, MemoryError, KeyboardInterrupt, SystemExit]


def invoke_according_to_policy(owner, error):
    """The legacy specific-error return remains; control/unlisted errors propagate."""
    if isinstance(error, ch.COM_PTR_EXCEPTIONS):
        assert owner.release() == 0
    else:
        with pytest.raises(type(error)) as raised:
            owner.release()
        assert raised.value is error


def patch_foreign_boundary(monkeypatch, receiver, callback):
    """Replace only vtable resolution with a Python callable boundary under test."""
    original = ch.ctypes.cast
    shape = SimpleNamespace(contents=SimpleNamespace(
        lpVtbl=SimpleNamespace(contents=SimpleNamespace(Release=callback))))
    def cast(value, destination):
        if destination == ctypes.POINTER(ch.IUnknown):
            return shape
        if value is shape:
            return ctypes.c_void_p(receiver.address)
        return original(value, destination)
    monkeypatch.setattr(ch.ctypes, "cast", cast)


@pytest.mark.parametrize("error_type", ERRORS)
def test_before_dispatch_error_preserves_pointer_and_requires_explicit_retry(monkeypatch, error_type):
    receiver = Receiver(2)
    owner = ch.ComPtr(receiver.ptr)
    error = error_type("before-dispatch exact error")
    def fail(*_args):
        raise error
    try:
        with monkeypatch.context() as patch:
            patch.setattr(ch.ctypes, "cast", fail)
            invoke_according_to_policy(owner, error)
        snapshot = owner.release_snapshot
        assert snapshot.state.value == "not_dispatched" and snapshot.error is error
        assert snapshot.refcount is None and owner.ptr.value == receiver.address
        assert not receiver.calls
        ch.ComPtr.__del__(owner)  # Finalization is not a new explicit release attempt.
        assert not receiver.calls and owner.release_snapshot == snapshot
        assert owner.release() == 1
        assert receiver.calls == [1]
    finally:
        owner.detach()


@pytest.mark.parametrize("error_type", ERRORS)
@pytest.mark.parametrize("effect_happened", [False, True])
def test_exception_after_dispatch_is_uncertain_not_automatically_retried(
        monkeypatch, error_type, effect_happened):
    receiver = Receiver(2)
    owner = ch.ComPtr(receiver.ptr)
    error = error_type("after-dispatch exact error")
    entries = []
    def callback(this):
        entries.append(this.value)
        if effect_happened:
            receiver.callbacks[2](this)
        raise error
    try:
        with monkeypatch.context() as patch:
            patch_foreign_boundary(patch, receiver, callback)
            invoke_according_to_policy(owner, error)
        snapshot = owner.release_snapshot
        assert snapshot.state.value == "uncertain" and snapshot.error is error
        assert snapshot.address == receiver.address and snapshot.refcount is None
        assert owner.ptr is None and len(entries) == 1
        with pytest.raises(RuntimeError, match="explicit ownership review"):
            owner.release()
        safe_release(owner)
        ch.ComPtr.__del__(owner)
        with pytest.raises(RuntimeError, match="No proven undispatched"):
            owner.detach_unreleased()
        assert len(entries) == 1 and owner.release_snapshot == snapshot
        assert receiver.refs == (1 if effect_happened else 2)
    finally:
        owner.detach()


def test_failed_return_conversion_is_also_uncertain(monkeypatch):
    receiver = Receiver(2)
    owner = ch.ComPtr(receiver.ptr)
    error = ValueError("invalid native-shaped return conversion")
    class BadReturn:
        def __int__(self):
            raise error
    def callback(this):
        receiver.callbacks[2](this)
        return BadReturn()
    try:
        with monkeypatch.context() as patch:
            patch_foreign_boundary(patch, receiver, callback)
            assert owner.release() == 0
        assert owner.release_snapshot.state.value == "uncertain"
        assert owner.release_snapshot.error is error
        assert owner.ptr is None and receiver.calls == [1]
    finally:
        owner.detach()


@pytest.mark.parametrize("method", ["attach", "adopt"])
def test_replacement_failure_keeps_both_ownerships_without_automatic_retry(monkeypatch, method):
    old, new = Receiver(), Receiver()
    owner = ch.ComPtr(old.ptr)
    error = TypeError("old-reference cast rejected")
    original = ch.ctypes.cast
    def reject_old(value, destination):
        if destination == ctypes.POINTER(ch.IUnknown):
            raise error
        return original(value, destination)
    held = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(ch.ctypes, "cast", reject_old)
            getattr(owner, method)(new.ptr)
        assert owner.ptr.value == new.address
        assert owner.release_snapshot.state.value == "held_not_dispatched"
        assert owner.release_snapshot.error is error and not old.calls and not new.calls
        with pytest.raises(RuntimeError, match="explicit ownership review"):
            owner.release()
        held = owner.detach_unreleased()
        assert held.value == old.address and owner.ptr.value == new.address
        assert owner.release_snapshot.state.value == "transferred"
        with pytest.raises(RuntimeError, match="No proven undispatched"):
            owner.detach_unreleased()
        ch.ComPtr(held).release()
        held = None
        owner.release()
        assert old.calls == new.calls == [0]
    finally:
        owner.detach()
        if held is not None:
            ch.ComPtr(held).release()


@pytest.mark.parametrize("method", ["attach", "adopt"])
def test_new_attachment_survives_old_uncertain_release(monkeypatch, method):
    old, new, rejected = Receiver(2), Receiver(), Receiver()
    owner = ch.ComPtr(old.ptr)
    error = RuntimeError("old cleanup outcome unknown")
    def callback(this):
        old.callbacks[2](this)
        getattr(owner, method)(new.ptr)
        raise error
    try:
        with monkeypatch.context() as patch:
            patch_foreign_boundary(patch, old, callback)
            assert owner.release() == 0
        assert owner.ptr.value == new.address
        snapshot = owner.release_snapshot
        assert snapshot.address == old.address and snapshot.error is error
        with pytest.raises(RuntimeError, match="explicit ownership review"):
            getattr(owner, method)(rejected.ptr)
        assert not rejected.calls and owner.ptr.value == new.address
        # Transfer the separately held current slot, never the uncertain old one.
        transferred = owner.detach()
        ch.ComPtr(transferred).release()
        assert new.calls == [0] and old.calls == [1]
        assert owner.release_snapshot == snapshot
    finally:
        owner.detach()
        ch.ComPtr(rejected.ptr).release()


@pytest.mark.parametrize("method", ["attach", "adopt"])
def test_preparing_state_rejects_transfer_without_claiming_incoming(monkeypatch, method):
    old, incoming = Receiver(), Receiver()
    owner = ch.ComPtr(old.ptr)
    observations = []
    original = ch.ctypes.cast
    def inspect(value, destination):
        if destination == ctypes.POINTER(ch.IUnknown):
            with pytest.raises(RuntimeError, match="already in progress"):
                getattr(owner, method)(incoming.ptr)
            observations.append(owner.ptr)
        return original(value, destination)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(ch.ctypes, "cast", inspect)
            owner.release()
        assert observations == [None] and old.calls == [0] and not incoming.calls
    finally:
        owner.detach()
        ch.ComPtr(incoming.ptr).release()


@pytest.mark.parametrize("missing", ["vtable", "release"])
def test_missing_vtable_or_callback_never_dispatches(monkeypatch, missing):
    receiver = Receiver()
    owner = ch.ComPtr(receiver.ptr)
    calls = []
    class MissingRelease:
        def __bool__(self):
            return False
        def __call__(self, _this):
            calls.append("invalid dispatch")
            raise RuntimeError("A null callback must not be invoked")
    try:
        with monkeypatch.context() as patch:
            if missing == "vtable":
                receiver.storage.lpVtbl = ctypes.POINTER(IUnknownVtbl)()
            else:
                # Do not execute a real null function on the faulty baseline.
                patch_foreign_boundary(patch, receiver, MissingRelease())
            assert owner.release() == 0
            snapshot = owner.release_snapshot
            assert snapshot.state.value == "not_dispatched"
            assert isinstance(snapshot.error, ValueError) and not receiver.calls
            assert not calls
            ch.ComPtr.__del__(owner)
            assert not receiver.calls and not calls
    finally:
        owner.detach()


def test_allocation_failure_before_claim_preserves_existing_state(monkeypatch):
    receiver = Receiver()
    owner = ch.ComPtr(receiver.ptr)
    error = MemoryError("bounded attempt allocation failed")
    def fail():
        raise error
    try:
        with monkeypatch.context() as patch:
            patch.setattr(ch, "_ReleaseAttempt", fail)
            with pytest.raises(MemoryError) as raised:
                owner.release()
            assert raised.value is error
        assert owner.ptr.value == receiver.address
        assert owner.release_snapshot is None and not receiver.calls
        owner.release()
        assert receiver.calls == [0]
    finally:
        owner.detach()


def test_gc_does_not_retry_a_failure_after_temporary_cast_patch_ends(monkeypatch):
    receiver = Receiver()
    gc.collect()
    before = gc.isenabled()
    gc.disable()
    try:
        def create_failed_owner():
            owner = ch.ComPtr(receiver.ptr)
            def fail(*_args):
                raise TypeError("retained failure creates traceback cycle")
            with monkeypatch.context() as patch:
                patch.setattr(ch.ctypes, "cast", fail)
                assert owner.release() == 0
        create_failed_owner()
        assert not receiver.calls
        gc.collect()
        assert not receiver.calls  # No retry when actual cast is available again.
    finally:
        if before:
            gc.enable()


def test_falsey_exception_is_retained_without_boolean_conversion(monkeypatch):
    receiver = Receiver()
    owner = ch.ComPtr(receiver.ptr)
    class FalseyError(TypeError):
        def __bool__(self):
            raise AssertionError("Do not coerce failure objects to bool")
    error = FalseyError("exact failure")
    def fail(*_args):
        raise error
    try:
        with monkeypatch.context() as patch:
            patch.setattr(ch.ctypes, "cast", fail)
            assert owner.release() == 0
        assert owner.release_snapshot.error is error
        owner.release()
        assert receiver.calls == [0]
    finally:
        owner.detach()


def test_success_inside_an_unrelated_exception_handler_has_no_failure():
    receiver = Receiver()
    owner = ch.ComPtr(receiver.ptr)
    try:
        try:
            raise ValueError("unrelated caller exception")
        except ValueError:
            owner.release()
            snapshot = owner.release_snapshot
            assert snapshot.state.value == "released" and snapshot.error is None
            assert snapshot.refcount == 0
    finally:
        owner.detach()
