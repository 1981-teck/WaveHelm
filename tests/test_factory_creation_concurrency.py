"""Real-thread factory coordination with inert values, never native COM success."""
from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from src.video import adapter_factory as factory
from tests.test_factory_creation_cleanup import FactoryToken
from tests.test_factory_creation_cleanup import isolated_factory as isolated_factory


class ObservedCondition(threading.Condition):
    """Observe real waits/notifications without replacing their synchronization."""

    def __init__(self) -> None:
        super().__init__(factory._adapter_lock)
        self.waiters: queue.Queue[str] = queue.Queue()
        self.notices: list[tuple[bool, int | None]] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.waiters.put(threading.current_thread().name)
        return super().wait(timeout)

    def notify_all(self) -> None:
        self.notices.append((factory._state.creating, factory._state.creator_tid))
        super().notify_all()


@dataclass
class RunningCall:
    """Capture a worker's result/error for assertions on the main test thread."""

    done: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: BaseException | None = None
    thread: threading.Thread | None = None

    def start(self, operation: Callable[[], object], name: str) -> RunningCall:
        def execute() -> None:
            try:
                self.result = operation()
            except BaseException as error:
                # Test observation boundary: retain control errors, never swallow a result.
                self.error = error
            finally:
                self.done.set()
        self.thread = threading.Thread(target=execute, name=name)
        self.thread.start()
        return self

    def join(self) -> None:
        assert self.done.wait(2), "Worker did not complete within the test coordination budget"
        assert self.thread is not None
        self.thread.join(2)
        assert not self.thread.is_alive()


def release_workers(events: list[threading.Event], workers: list[RunningCall]) -> None:
    """Release test barriers even on RED runs, then join every created worker."""
    for event in events:
        event.set()
    with factory._adapter_lock:
        factory._state.creating = False
        factory._state.creator_tid = None
        factory._state.callback_running = False
        factory._state.callback_tid = None
        factory._adapter_cv.notify_all()
    for worker in workers:
        worker.join()


@pytest.mark.parametrize("kind", (MemoryError, KeyError, KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("waiter_count", (1, 3))
def test_aborted_constructor_wakes_real_waiters_and_releases_owner(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch,
    kind: type[BaseException], waiter_count: int,
) -> None:
    started, release = threading.Event(), threading.Event()
    cv = ObservedCondition()
    monkeypatch.setattr(factory, "_adapter_cv", cv)
    error = kind("WH_SYNTHETIC_08G_worker_failure")
    calls: list[int] = []

    def constructor(event_bus: object = None) -> None:
        calls.append(threading.get_ident())
        started.set()
        assert release.wait(3)
        raise error

    monkeypatch.setattr(factory, "create_imf_media_engine_adapter", constructor)
    workers = [RunningCall().start(factory.create_best_video_adapter, "creator")]
    try:
        assert started.wait(2)
        for index in range(waiter_count):
            workers.append(RunningCall().start(factory.create_best_video_adapter, f"waiter-{index}"))
            assert cv.waiters.get(timeout=2).startswith("waiter-")
        release.set()
        for worker in workers:
            worker.join()
        assert workers[0].error is error and len(calls) == 1
        assert all(isinstance(item.error, factory.MediaEngineError) for item in workers[1:])
        assert all("unknown failure" in str(item.error) for item in workers[1:])
        assert (False, None) in cv.notices
        assert factory.get_adapter_status()["creating"] is False
        assert factory.get_adapter_status()["creator_tid"] is None
    finally:
        release_workers([release], workers)


def test_success_is_published_once_to_multiple_real_waiters(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = threading.Event(), threading.Event()
    cv, token = ObservedCondition(), FactoryToken()
    calls: list[object] = []
    callbacks: list[object] = []
    monkeypatch.setattr(factory, "_adapter_cv", cv)
    factory.set_adapter_creation_callback(callbacks.append)

    def constructor(event_bus: object = None) -> FactoryToken:
        calls.append(event_bus)
        started.set()
        assert release.wait(3)
        return token

    monkeypatch.setattr(factory, "create_imf_media_engine_adapter", constructor)
    workers = [RunningCall().start(factory.create_best_video_adapter, "creator")]
    try:
        assert started.wait(2)
        for index in range(3):
            workers.append(RunningCall().start(factory.create_best_video_adapter, f"waiter-{index}"))
            cv.waiters.get(timeout=2)
        release.set()
        for worker in workers:
            worker.join()
        assert all(item.result is token and item.error is None for item in workers)
        assert calls == [None] and callbacks == [token]
        assert factory.get_adapter_status()["creation_seq"] == 1
    finally:
        release_workers([release], workers)


def test_shutdown_waits_for_owned_callback_before_detaching_token(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = threading.Event(), threading.Event()
    cv, token = ObservedCondition(), FactoryToken()
    monkeypatch.setattr(factory, "_adapter_cv", cv)
    monkeypatch.setattr(factory, "create_imf_media_engine_adapter", lambda event_bus=None: token)

    def callback(adapter: object) -> None:
        started.set()
        assert release.wait(3)
        assert adapter is token and token.shutdown_calls == 0

    factory.set_adapter_creation_callback(callback)
    workers = [RunningCall().start(factory.create_best_video_adapter, "creator")]
    try:
        assert started.wait(2)
        workers.append(RunningCall().start(factory.shutdown_video_adapter, "shutdown"))
        assert cv.waiters.get(timeout=2) == "shutdown"
        assert not workers[1].done.is_set() and token.shutdown_calls == 0
        release.set()
        for worker in workers:
            worker.join()
        assert all(item.error is None for item in workers)
        assert token.shutdown_calls == 1 and factory._state.instance is None
        assert factory.get_adapter_status()["callback_running"] is False
    finally:
        release_workers([release], workers)


def test_old_failure_finalizer_cannot_clear_a_new_creator(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    logging_first, finish_first = threading.Event(), threading.Event()
    second_started, finish_second = threading.Event(), threading.Event()
    original = ValueError("first failure")

    def constructor(event_bus: object = None) -> None:
        if threading.current_thread().name == "first":
            raise original
        second_started.set()
        assert finish_second.wait(3)
        raise KeyError("second failure")

    def error_log(*args: object, **kwargs: object) -> None:
        logging_first.set()
        assert finish_first.wait(3)

    monkeypatch.setattr(factory, "create_imf_media_engine_adapter", constructor)
    monkeypatch.setattr(factory.logger, "error", error_log)
    workers = [RunningCall().start(factory.create_best_video_adapter, "first")]
    try:
        assert logging_first.wait(2)
        workers.append(RunningCall().start(factory.create_best_video_adapter, "second"))
        assert second_started.wait(2)
        expected = factory.get_adapter_status()
        finish_first.set()
        workers[0].join()
        assert workers[0].error is original
        assert factory.get_adapter_status() == expected
        assert expected["creating"] is True and expected["creation_seq"] == 2
        finish_second.set()
        workers[1].join()
        assert isinstance(workers[1].error, KeyError)
        assert factory.get_adapter_status()["creating"] is False
    finally:
        release_workers([finish_first, finish_second], workers)


def test_old_callback_finalizer_cannot_clear_another_threads_callback(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_detached, second_started = threading.Event(), threading.Event()
    finish_first, finish_second = threading.Event(), threading.Event()
    first_token, second_token = FactoryToken(), FactoryToken()

    def constructor(event_bus: object = None) -> FactoryToken:
        return first_token if threading.current_thread().name == "first" else second_token

    def callback(adapter: object) -> None:
        if adapter is first_token:
            factory.shutdown_video_adapter()
            first_detached.set()
            assert finish_first.wait(3)
        else:
            second_started.set()
            assert finish_second.wait(3)

    monkeypatch.setattr(factory, "create_imf_media_engine_adapter", constructor)
    factory.set_adapter_creation_callback(callback)
    workers = [RunningCall().start(factory.create_best_video_adapter, "first")]
    try:
        assert first_detached.wait(2)
        workers.append(RunningCall().start(factory.create_best_video_adapter, "second"))
        assert second_started.wait(2)
        expected = factory.get_adapter_status()
        finish_first.set()
        workers[0].join()
        assert factory.get_adapter_status() == expected
        assert expected["callback_running"] is True and expected["creation_seq"] == 2
        finish_second.set()
        workers[1].join()
        assert all(item.error is None for item in workers)
        assert factory._state.instance is second_token
        assert factory.get_adapter_status()["callback_running"] is False
        assert first_token.shutdown_calls == 1 and second_token.shutdown_calls == 0
    finally:
        release_workers([finish_first, finish_second], workers)
