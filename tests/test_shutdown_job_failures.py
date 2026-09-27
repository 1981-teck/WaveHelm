"""Failure and admission contracts for 08Q's single-execution cleanup.

Exceptions are raised in Python boundaries, never across native ctypes callbacks.
Uncertain resource disposition is retained; these tests do not promise reclamation.
"""
from __future__ import annotations

import math
import queue
import threading

import pytest

import src.video.media_engine_core_shutdown as cleanup
from src.video.media_engine_core import MediaEngineCore
from tests.test_comptr_release_ownership import Receiver
from tests.test_media_engine_core_shutdown import DummyAdapter
from tests.test_shutdown_job_execution import Adapter, Core, Worker


@pytest.mark.parametrize("error_type", [
    RuntimeError, ValueError, OSError, KeyError, MemoryError, KeyboardInterrupt, SystemExit,
])
def test_error_after_claim_is_terminal_retains_identity_and_never_replays(error_type):
    receiver = Receiver(2)
    core = Core(receiver, DummyAdapter())
    original = error_type("controlled failure after claim")
    def fail():
        raise original
    core.hook = fail
    if isinstance(original, cleanup.MEDIA_ENGINE_CORE_SHUTDOWN_SYNC_EXCEPTIONS):
        cleanup.shutdown(core)
    else:
        with pytest.raises(error_type) as info:
            cleanup.shutdown(core)
        assert info.value is original
    before = cleanup.get_shutdown_snapshot(core)
    assert before.execution == "FAILED_UNCERTAIN"
    assert before.error is original
    assert before.transport_error is original
    assert core._shutdown_job._resources is not None
    core.hook = None
    core._shutdown_job.run()
    cleanup.shutdown(core)
    MediaEngineCore.__del__(core)
    assert [name for name, _ in core.actions] == ["stop", "notify"]
    assert receiver.calls == []


@pytest.mark.parametrize("phase", ["stop", "engine", "release", "notify"])
def test_failure_at_each_native_boundary_never_retries_prior_effects(phase, monkeypatch):
    receiver = Receiver(2)
    core = Core(receiver, DummyAdapter())
    original = RuntimeError(phase)
    def fail(*_args):
        raise original
    if phase == "stop":
        core.hook = fail
    elif phase == "engine":
        core._shutdown_engine_ptr = fail
    elif phase == "release":
        import src.video.component_base.utils as utils
        monkeypatch.setattr(utils, "safe_release", fail)
    else:
        core._release_notify_iunknown = fail
    cleanup.shutdown(core)
    actions, calls = list(core.actions), list(receiver.calls)
    core._shutdown_job.run()
    assert core.actions == actions and receiver.calls == calls
    assert cleanup.get_shutdown_snapshot(core).error is original


def test_notify_failure_keeps_the_primary_error_in_its_context():
    receiver = Receiver(2)
    core = Core(receiver, DummyAdapter())
    first, last = RuntimeError("stop"), ValueError("notify")
    def stop():
        raise first
    def notify(*_):
        raise last
    core.hook, core._release_notify_iunknown = stop, notify
    cleanup.shutdown(core)
    snapshot = cleanup.get_shutdown_snapshot(core)
    assert snapshot.error is last and last.__context__ is first
    core._shutdown_job.run()
    assert not receiver.calls


def test_outer_exception_does_not_contaminate_success_receipt():
    receiver = Receiver(2)
    core = Core(receiver, DummyAdapter())
    try:
        raise RuntimeError("unrelated")
    except RuntimeError:
        cleanup.shutdown(core)
    snapshot = cleanup.get_shutdown_snapshot(core)
    assert snapshot.execution == "RETURNED" and snapshot.error is None
    assert snapshot.transport_error is None


def test_falsey_exception_is_retained_not_replaced_by_a_default():
    class FalseError(RuntimeError):
        def __bool__(self):
            return False
    error = FalseError("falsey")
    receiver = Receiver(2)
    core = Core(receiver, DummyAdapter(sync_exc=error))
    cleanup.shutdown(core)
    assert cleanup.get_shutdown_snapshot(core).transport_error is error
    assert not core.actions


@pytest.mark.parametrize("error_type", [RuntimeError, KeyError, MemoryError, KeyboardInterrupt, SystemExit])
def test_exception_after_enqueue_keeps_one_eligible_callback(error_type):
    worker = Worker()
    receiver = Receiver(2)
    core = Core(receiver, Adapter(worker))
    error = error_type("wake after enqueue")
    def fail():
        raise error
    worker._wake_com_thread = fail
    try:
        if error_type is RuntimeError:
            cleanup.shutdown(core)  # Known wake failure is logged; admission survives.
            assert cleanup.get_shutdown_snapshot(core).admission == "ACCEPTED"
        else:
            with pytest.raises(error_type) as info:
                cleanup.shutdown(core)
            assert info.value is error
            assert cleanup.get_shutdown_snapshot(core).admission == "UNCONFIRMED"
        assert worker._task_queue.qsize() == 1 and not core.actions
        cleanup.shutdown(core)
        assert worker._task_queue.qsize() == 1
    finally:
        worker.finish()
    assert receiver.calls == [1]
    assert cleanup.get_shutdown_snapshot(core).execution == "RETURNED"


@pytest.mark.parametrize("error_type", [RuntimeError, MemoryError, KeyboardInterrupt, SystemExit])
def test_pre_enqueue_exception_keeps_resources_without_local_fallback(error_type):
    worker = Worker()
    receiver = Receiver(2)
    core = Core(receiver, Adapter(worker))
    error = error_type("enqueue failed")
    class RejectQueue(queue.Queue):
        def put_nowait(self, value):
            raise error
    worker._task_queue = RejectQueue()
    try:
        if error_type is RuntimeError:
            cleanup.shutdown(core)
        else:
            with pytest.raises(error_type) as info:
                cleanup.shutdown(core)
            assert info.value is error
        snapshot = cleanup.get_shutdown_snapshot(core)
        assert snapshot.execution == "PREPARED" and snapshot.admission == "UNCONFIRMED"
        assert snapshot.transport_error is error
        assert worker._task_queue.empty() and not receiver.calls and not core.actions
        assert core._shutdown_job._resources is not None
    finally:
        worker.finish()


def test_bare_reply_does_not_invent_callback_completion():
    job = cleanup._ShutdownJob(lambda *_: None)
    job.put_nowait(None)
    assert job.snapshot().reply_seen and job.snapshot().execution == "PREPARED"
    assert job.wait(0) is False
    assert job.snapshot().wait_expired


def test_worker_rejection_is_terminal_even_if_a_late_callback_survives():
    receiver = Receiver(2)
    core = Core(receiver, DummyAdapter(sync_exc=RuntimeError("submission unknown")))
    cleanup.shutdown(core)
    error = RuntimeError("worker rejection")
    core._shutdown_job.put_nowait(error)
    core._shutdown_job.put_nowait(RuntimeError("duplicate reply"))
    core._shutdown_job.run()
    snapshot = cleanup.get_shutdown_snapshot(core)
    assert snapshot.execution == "REJECTED" and snapshot.error is error
    assert snapshot.transport_error is error and not receiver.calls and not core.actions


@pytest.mark.parametrize("timeout", [-1, math.nan, math.inf, -math.inf, threading.TIMEOUT_MAX * 2])
def test_invalid_wait_budget_cannot_admit_cleanup(timeout):
    worker = Worker()
    worker.com_task_timeout = timeout
    receiver = Receiver(2)
    core = Core(receiver, Adapter(worker))
    try:
        cleanup.shutdown(core)
        assert worker._task_queue.empty()
        assert isinstance(cleanup.get_shutdown_snapshot(core).transport_error, ValueError)
        assert cleanup.get_shutdown_snapshot(core).execution == "PREPARED"
    finally:
        worker.finish()
    assert not receiver.calls


@pytest.mark.parametrize("bad_value", [None, 1, "yes"])
def test_admission_requires_a_boolean_not_truthiness(bad_value):
    job = cleanup._ShutdownJob(lambda *_: None)
    with pytest.raises(TypeError):
        job.set_admission(bad_value)
    assert job.snapshot().admission == "NOT_SUBMITTED"


def test_negative_admission_after_execution_is_explicitly_inconsistent():
    receiver = Receiver(2)
    core = Core(receiver, DummyAdapter())
    cleanup.shutdown(core)
    with pytest.raises(RuntimeError, match="negative admission"):
        core._shutdown_job.set_admission(False)
    snapshot = cleanup.get_shutdown_snapshot(core)
    assert snapshot.execution == "RETURNED" and snapshot.admission == "INCONSISTENT"
    assert receiver.calls == [1]


def test_job_allocation_failure_does_not_detach_core(monkeypatch):
    receiver = Receiver(2)
    core = Core(receiver, DummyAdapter())
    pointer, cache = core._media_engine, core._vtable_call_cache
    original = MemoryError("job allocation")
    def fail(*_):
        raise original
    monkeypatch.setattr(cleanup, "_ShutdownJob", fail)
    with pytest.raises(MemoryError) as info:
        cleanup.shutdown(core)
    assert info.value is original
    assert not core._shutdown_requested and core._media_engine is pointer
    assert core._vtable_call_cache is cache and not receiver.calls


def test_resource_snapshot_failure_does_not_detach_core(monkeypatch):
    receiver = Receiver(2)
    core = Core(receiver, DummyAdapter())
    pointer = core._media_engine
    original = MemoryError("resource tuple")
    def fail(*_):
        raise original
    monkeypatch.setattr(cleanup, "_ShutdownResources", fail)
    with pytest.raises(MemoryError) as info:
        cleanup.shutdown(core)
    assert info.value is original
    assert not core._shutdown_requested and core._media_engine is pointer
    assert cleanup.get_shutdown_snapshot(core) is None


def test_new_core_fields_are_not_erased_by_old_job_completion():
    worker = Worker()
    receiver = Receiver(2)
    core = Core(receiver, Adapter(worker))
    try:
        cleanup.shutdown(core)
        replacement = object()
        core._media_engine = replacement  # Observation of generation boundary only.
    finally:
        worker.finish()
    assert core._media_engine is replacement and receiver.calls == [1]


@pytest.mark.parametrize("field", ["_closed", "_shutdown_requested"])
def test_adapter_closing_does_not_cancel_already_owned_cleanup(field):
    worker = Worker()
    receiver = Receiver(2)
    adapter = Adapter(worker)
    setattr(adapter, field, True)
    core = Core(receiver, adapter)
    try:
        cleanup.shutdown(core)
        adapter._core = object()
        adapter._source = "new-source"
    finally:
        worker.finish()
    assert cleanup.get_shutdown_snapshot(core).execution == "RETURNED"
    assert receiver.calls == [1]


def test_successful_native_callback_with_lost_inline_reply_stays_observed():
    worker = Worker()
    receiver = Receiver(2)
    core = Core(receiver, Adapter(worker))
    errors = []
    original = queue.Full("reply loss after return")
    def outer():
        job = cleanup._detach_shutdown_resources(core)
        def fail_reply(_result):
            raise original
        job.put_nowait = fail_reply
        try:
            cleanup._dispatch_shutdown(core._adapter, job)
        except queue.Full as error:
            errors.append(error)
    try:
        assert worker.submit_cleanup_to_com_thread("outer", outer, queue.Queue()) is True
    finally:
        worker.finish()
    snapshot = cleanup.get_shutdown_snapshot(core)
    assert errors == [original] and snapshot.execution == "RETURNED"
    assert snapshot.transport_error is original
    core._shutdown_job.run()
    assert receiver.calls == [1] and len(core.actions) == 3


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, MemoryError])
def test_tracked_inline_control_failure_is_terminal(error_type):
    worker = Worker()
    receiver = Receiver(2)
    core = Core(receiver, Adapter(worker))
    original = error_type("inline interruption")
    errors = []
    def fail():
        raise original
    core.hook = fail
    def outer():
        try:
            cleanup.shutdown(core)
        except (KeyboardInterrupt, SystemExit, MemoryError) as error:
            errors.append(error)
    try:
        assert worker.submit_cleanup_to_com_thread("outer", outer, queue.Queue()) is True
    finally:
        worker.finish()
    snapshot = cleanup.get_shutdown_snapshot(core)
    assert errors == [original] and snapshot.error is original
    assert snapshot.execution == "FAILED_UNCERTAIN"
    core._shutdown_job.run()
    assert receiver.calls == [] and len(core.actions) == 2


@pytest.mark.parametrize("name, fn, response", [
    ("", lambda: None, queue.Queue()),
    ("x" * 65, lambda: None, queue.Queue()),
    (None, lambda: None, queue.Queue()),
    ("cleanup", None, queue.Queue()),
    ("cleanup", lambda: None, object()),
])
def test_invalid_submission_does_not_enqueue(name, fn, response):
    worker = Worker()
    try:
        assert worker.submit_cleanup_to_com_thread(name, fn, response) is False
        assert worker._task_queue.empty()
    finally:
        worker.finish()
