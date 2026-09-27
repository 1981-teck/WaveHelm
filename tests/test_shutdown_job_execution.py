"""08Q teardown protocol tests using real queues/threads and retained test storage.

No COM apartment, MediaEngine or freed memory is simulated as valid. The native
effects below record order; Receiver maintains an independent reference ledger.
"""
from __future__ import annotations

import queue
import threading

import pytest

from src.video.component_adapter.com_thread_manager import ComThreadManager
import src.video.component_base.utils as utils
import src.video.media_engine_core_shutdown as cleanup
import src.video.imf_media_engine_adapter_threading as adapter_threading
from tests.test_comptr_release_ownership import Receiver
from tests.test_media_engine_core_shutdown import DummyCore, DummyAdapter


class Worker(ComThreadManager):
    """Actual manager admission/drain; an ordinary thread gated before execution."""
    def __init__(self):
        super().__init__("08Q-test-worker", com_task_timeout=0.02)
        self.gate = threading.Event()
        self.finished = threading.Event()
        self.errors = []
        self._com_ready_event.set()
        self.worker = threading.Thread(target=self._work, daemon=True)
        self._com_thread = self.worker
        self.worker.start()

    def _is_on_com_thread(self):
        return threading.current_thread() is self.worker

    def _wake_com_thread(self):
        pass

    def _work(self):
        try:
            if not self.gate.wait(4):
                self.errors.append("test scheduling gate expired")
                return
            self._drain_tasks_com_thread()
        except (KeyboardInterrupt, SystemExit) as error:
            self.errors.append(error)
        finally:
            self.finished.set()

    def finish(self):
        self.gate.set()
        self.worker.join(5)
        assert not self.worker.is_alive()
        assert self.finished.is_set()


class Adapter:
    def call_shutdown_on_com_thread(self, name, func, response):
        return adapter_threading.call_shutdown_on_com_thread(self, name, func, response)

    def __init__(self, worker):
        self._lock = threading.RLock()
        self.legacy_calls = []
        self._com_thread_manager = worker
        self._shutdown_requested = True
        self._closed = True  # Detached cleanup must survive adapter closure.

    def call_on_com_thread(self, *args):
        self.legacy_calls.append("call")
        return self._com_thread_manager.call_on_com_thread(*args)

    def post_to_com_thread(self, *args):
        self.legacy_calls.append("post")
        return self._com_thread_manager.post_to_com_thread(*args)


class Core(DummyCore):
    _shutdown_and_release_on_com_thread = cleanup._shutdown_and_release_on_com_thread
    _shutdown_and_release_local = cleanup._shutdown_and_release_local

    def __init__(self, receiver, adapter):
        super().__init__(adapter)
        self.receiver = receiver
        self._media_engine = receiver.ptr
        self._media_engine_ex = self._factory = self._attributes = None
        self._notify_iunknown = self._notify_handler = None
        self.actions = []
        self.hook = None

    def _stop_engine_ptr_playback(self, pointer):
        self.actions.append(("stop", threading.get_ident()))
        if self.hook is not None:
            self.hook()

    def _shutdown_engine_ptr(self, pointer):
        self.actions.append(("shutdown", threading.get_ident()))

    def _release_notify_iunknown(self, pointer):
        self.actions.append(("notify", threading.get_ident()))


@pytest.fixture(autouse=True)
def isolated_address_registry(monkeypatch):
    monkeypatch.setattr(utils, "_released_ptrs", {})
    monkeypatch.setattr(utils, "_last_purge_monotonic", 0.0)
    assert utils._RELEASED_PTR_TTL_SEC == 60.0


@pytest.mark.parametrize("references", [1, 2, 3])
def test_real_timeout_retains_one_task_and_one_bundle_execution(references):
    worker = Worker()
    receiver = Receiver(references)
    core = Core(receiver, Adapter(worker))
    try:
        cleanup.shutdown(core)
        before = cleanup.get_shutdown_snapshot(core)
        assert before.admission == "ACCEPTED"
        assert before.execution == "PREPARED" and before.wait_expired
        assert worker._task_queue.qsize() == 1 and not core.actions
        assert core._adapter.legacy_calls == []
        cleanup.shutdown(core)
        assert worker._task_queue.qsize() == 1
    finally:
        worker.finish()
    after = cleanup.get_shutdown_snapshot(core)
    assert after.execution == "RETURNED" and after.wait_expired and after.reply_seen
    assert receiver.calls == [references - 1]
    assert [name for name, _ in core.actions] == ["stop", "shutdown", "notify"]
    assert {tid for _, tid in core.actions} == {worker.worker.ident}
    assert not worker.errors


@pytest.mark.parametrize("copies", [2, 4])
def test_duplicate_queued_callbacks_cannot_replay_any_effect(copies):
    worker = Worker()
    receiver = Receiver(2)
    core = Core(receiver, Adapter(worker))
    try:
        cleanup.shutdown(core)
        task = worker._task_queue.queue[0]  # Worker is held behind its gate.
        for _ in range(copies - 1):
            worker._task_queue.put_nowait(task)
    finally:
        worker.finish()
    assert receiver.calls == [1]
    assert [name for name, _ in core.actions] == ["stop", "shutdown", "notify"]


def test_reentrant_job_and_reentrant_shutdown_have_no_second_effect():
    receiver = Receiver(2)
    core = Core(receiver, DummyAdapter())
    def hook():
        assert not core._state_lock._is_owned()
        assert not core._shutdown_job._lock._is_owned()
        assert cleanup.get_shutdown_snapshot(core).execution == "RUNNING"
        cleanup.shutdown(core)
        core._shutdown_job.run()
    core.hook = hook
    cleanup.shutdown(core)
    assert receiver.calls == [1] and len(core.actions) == 3
    assert cleanup.get_shutdown_snapshot(core).execution == "RETURNED"


def test_overlapping_callback_claim_is_exclusive_without_holding_state_lock():
    worker = Worker()
    receiver = Receiver(2)
    core = Core(receiver, Adapter(worker))
    entered, resume = threading.Event(), threading.Event()
    observations = []
    def hook():
        observations.append((core._state_lock._is_owned(),
                             core._shutdown_job._lock._is_owned(),
                             worker._lock._is_owned()))
        entered.set()
        if not resume.wait(3):
            observations.append("expired")
    core.hook = hook
    try:
        cleanup.shutdown(core)
        worker.gate.set()
        assert entered.wait(2)
        core._shutdown_job.run()
        assert len(core.actions) == 1 and receiver.calls == []
    finally:
        resume.set()
        worker.finish()
    assert observations == [(False, False, False)]
    assert receiver.calls == [1] and len(core.actions) == 3


def test_cleanup_invoked_on_worker_runs_inline_without_self_timeout():
    worker = Worker()
    receiver = Receiver(2)
    core = Core(receiver, Adapter(worker))
    result = queue.Queue()
    try:
        assert worker.submit_cleanup_to_com_thread(
            "outer", lambda: cleanup.shutdown(core), result) is True
    finally:
        worker.finish()
    result.get_nowait()
    snapshot = cleanup.get_shutdown_snapshot(core)
    assert snapshot.execution == "RETURNED" and snapshot.admission == "ACCEPTED"
    assert not snapshot.wait_expired
    assert receiver.calls == [1] and not worker.errors


@pytest.mark.parametrize("mode", ["shutdown", "stopped", "not_ready", "dead", "full"])
def test_refusal_does_not_run_local_or_claim_completion(mode):
    worker = Worker()
    receiver = Receiver(2)
    core = Core(receiver, Adapter(worker))
    if mode == "shutdown":
        worker._shutdown_requested = True
    elif mode == "stopped":
        worker._stop_event.set()
    elif mode == "not_ready":
        worker._com_ready_event.clear()
    elif mode == "dead":
        worker._com_thread = None
    else:
        class NonblockingQueue(queue.Queue):
            def put(self, item, block=True, timeout=None):
                if block:
                    raise AssertionError("A full cleanup queue must never use blocking put")
                return super().put(item, block=False)
        worker._task_queue = NonblockingQueue(maxsize=1)
        worker._task_queue.put_nowait("occupied")
    try:
        cleanup.shutdown(core)
        snapshot = cleanup.get_shutdown_snapshot(core)
        assert snapshot.admission == snapshot.execution == "REJECTED"
        assert core._shutdown_job._resources is not None
        core._shutdown_job.run()
        assert core.actions == [] and receiver.calls == []
    finally:
        if mode == "full":
            assert worker._task_queue.get_nowait() == "occupied"
        worker.finish()


@pytest.mark.parametrize("mode", ["reject_queue", "stop_before_drain", "replace_worker"])
def test_admitted_then_rejected_task_keeps_references_without_execution(mode):
    worker = Worker()
    receiver = Receiver(2)
    core = Core(receiver, Adapter(worker))
    try:
        cleanup.shutdown(core)
        if mode == "reject_queue":
            worker._reject_pending_tasks("test worker shutdown")
        elif mode == "stop_before_drain":
            worker._stop_event.set()
        else:
            worker._com_thread = threading.Thread()
    finally:
        worker.finish()
    snapshot = cleanup.get_shutdown_snapshot(core)
    assert snapshot.admission == "ACCEPTED" and snapshot.execution == "REJECTED"
    assert isinstance(snapshot.error, RuntimeError)
    assert snapshot.transport_error is snapshot.error
    core._shutdown_job.run()
    assert core.actions == [] and receiver.calls == []


def test_lost_reply_does_not_erase_observed_execution_or_trigger_retry():
    worker = Worker()
    receiver = Receiver(2)
    core = Core(receiver, Adapter(worker))
    class LostReply:
        def put_nowait(self, result):
            pass
    try:
        cleanup.shutdown(core)
        task = worker._task_queue.get_nowait()
        worker._task_queue.put_nowait(type(task)(
            task.name, task.fn, task.args, task.kwargs, LostReply()))
    finally:
        worker.finish()
    snapshot = cleanup.get_shutdown_snapshot(core)
    assert snapshot.execution == "RETURNED" and not snapshot.reply_seen
    cleanup.shutdown(core)
    assert core._shutdown_job.wait(0) is True
    assert receiver.calls == [1]


@pytest.mark.parametrize("closed", [False, True])
def test_absent_manager_is_rejected_without_implicit_start(closed):
    receiver = Receiver(2)
    adapter = Adapter(None)
    adapter._closed = adapter._shutdown_requested = closed
    core = Core(receiver, adapter)
    cleanup.shutdown(core)
    assert cleanup.get_shutdown_snapshot(core).execution == "REJECTED"
    assert not core.actions and not receiver.calls


def test_core_without_adapter_retains_bundle_and_never_falls_back_locally():
    receiver = Receiver(2)
    core = Core(receiver, None)
    cleanup.shutdown(core)
    assert cleanup.get_shutdown_snapshot(core).execution == "REJECTED"
    assert core._shutdown_job._resources is not None
    assert not receiver.calls and not core.actions


def test_equal_address_separate_engine_acquisitions_are_not_deduplicated():
    worker = Worker()
    receiver = Receiver(3)  # Base, Ex, and one retained external reference.
    core = Core(receiver, Adapter(worker))
    core._media_engine_ex = receiver.ptr
    try:
        cleanup.shutdown(core)
    finally:
        worker.finish()
    assert receiver.calls == [2, 1] and receiver.refs == 1


def test_submission_reservation_prevents_a_second_enqueue():
    worker = Worker()
    receiver = Receiver(2)
    adapter = Adapter(worker)
    core = Core(receiver, adapter)
    try:
        cleanup.shutdown(core)
        cleanup._dispatch_shutdown(adapter, core._shutdown_job)
        assert worker._task_queue.qsize() == 1
    finally:
        worker.finish()
    assert receiver.calls == [1]


def test_queue_insertion_is_atomic_with_manager_shutdown():
    """Force shutdown to request the admission lock during actual queue insertion."""
    worker = Worker()
    receiver = Receiver(2)
    core = Core(receiver, Adapter(worker))
    requested = threading.Event()
    stop_thread = None
    observations = []
    reject = worker._reject_pending_tasks
    def reject_then_unblock(reason):
        reject(reason)
        worker.gate.set()
    worker._reject_pending_tasks = reject_then_unblock
    def stop():
        requested.set()
        worker.shutdown()
    class AdmissionQueue(queue.Queue):
        def put_nowait(self, task):
            nonlocal stop_thread
            assert worker._lock._is_owned()
            stop_thread = threading.Thread(target=stop, daemon=True)
            stop_thread.start()
            assert requested.wait(2)
            observations.append(worker._shutdown_requested)
            return super().put_nowait(task)
    worker._task_queue = AdmissionQueue()
    try:
        cleanup.shutdown(core)
    finally:
        worker.finish()
        if stop_thread is not None:
            stop_thread.join(5)
            assert not stop_thread.is_alive()
    snapshot = cleanup.get_shutdown_snapshot(core)
    assert observations == [False]
    assert snapshot.admission == "ACCEPTED" and snapshot.execution == "REJECTED"
    assert worker._task_queue.empty()
    assert not receiver.calls and not core.actions
