from __future__ import annotations

import queue
import threading
import time

import pytest

from src.audio.audio_event_bus import AudioEventBus
from src.audio.audio_event_models import AudioEventType, EventBusShutdownError


class LockProbeExecutor:
    def __init__(self, bus: AudioEventBus) -> None:
        self._bus = bus
        self.calls: list[tuple[bool, bool]] = []
        self.lock_available = False

    def shutdown(self, wait: bool, cancel_futures: bool) -> None:
        self.calls.append((wait, cancel_futures))
        completed = threading.Event()

        def read_stats() -> None:
            self._bus.get_stats()
            completed.set()

        thread = threading.Thread(target=read_stats)
        thread.start()
        self.lock_available = completed.wait(timeout=1.0)
        thread.join(timeout=2.0)
        assert not thread.is_alive()


class FailingExecutor:
    def shutdown(self, wait: bool, cancel_futures: bool) -> None:
        raise OSError('executor shutdown failed')


def _wait_for_subscriber(bus: AudioEventBus, event_type: AudioEventType) -> None:
    deadline = time.monotonic() + 2.0
    while bus.get_subscriber_count(event_type) == 0:
        if time.monotonic() >= deadline:
            raise AssertionError('waiter subscription was not registered')
        time.sleep(0.001)


def test_shutdown_invokes_executor_outside_central_lock() -> None:
    bus = AudioEventBus()
    executor = LockProbeExecutor(bus)
    bus._enable_async = True
    bus._executor = executor

    bus.shutdown()

    assert executor.calls == [(True, True)]
    assert executor.lock_available is True
    assert bus.is_alive() is False
    assert bus.get_stats()['shutting_down'] is True


def test_shutdown_waits_for_active_synchronous_publish_before_clearing_state() -> None:
    bus = AudioEventBus()
    event_type = AudioEventType.PLAYBACK_STARTED
    callback_entered = threading.Event()
    release_callback = threading.Event()
    publish_result: queue.Queue[bool] = queue.Queue()
    shutdown_complete = threading.Event()

    def callback(data: object) -> None:
        callback_entered.set()
        assert release_callback.wait(timeout=2.0)

    bus.subscribe(event_type, callback)
    publisher = threading.Thread(
        target=lambda: publish_result.put(bus.publish(event_type, {'value': 1}))
    )
    publisher.start()
    assert callback_entered.wait(timeout=2.0)

    closer = threading.Thread(target=lambda: (bus.shutdown(), shutdown_complete.set()))
    closer.start()
    assert shutdown_complete.wait(timeout=0.05) is False

    release_callback.set()
    publisher.join(timeout=2.0)
    closer.join(timeout=2.0)
    assert not publisher.is_alive()
    assert not closer.is_alive()
    assert publish_result.get_nowait() is True
    assert shutdown_complete.is_set()
    assert bus.get_event_history() == []
    assert bus.get_subscriber_count(event_type) == 0


def test_shutdown_cancels_pending_waiter_without_waiting_for_timeout() -> None:
    bus = AudioEventBus()
    event_type = AudioEventType.PLAYBACK_STARTED
    errors: queue.Queue[Exception] = queue.Queue()

    def wait() -> None:
        try:
            bus.wait_for_event(event_type, timeout=60.0)
        except Exception as error:
            errors.put(error)

    thread = threading.Thread(target=wait)
    thread.start()
    _wait_for_subscriber(bus, event_type)

    bus.shutdown()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert isinstance(errors.get_nowait(), EventBusShutdownError)


def test_shutdown_clears_history_without_synthesizing_a_shutdown_record() -> None:
    bus = AudioEventBus()
    assert bus.publish(AudioEventType.PLAYBACK_STARTED, {'value': 1}) is True
    assert len(bus.get_event_history()) == 1

    bus.shutdown()

    assert bus.get_event_history() == []
    stats = bus.get_stats()
    assert stats['events_published'] == 1
    assert stats['history_size'] == 0
    assert 'event_bus_shutdown' not in stats['events_by_type']


def test_concurrent_shutdown_callers_observe_one_terminal_transition() -> None:
    bus = AudioEventBus()
    workers = 16
    barrier = threading.Barrier(workers)
    errors: queue.Queue[Exception] = queue.Queue()

    def close() -> None:
        try:
            barrier.wait(timeout=5.0)
            bus.shutdown()
        except Exception as error:
            errors.put(error)

    threads = [threading.Thread(target=close) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)
        assert not thread.is_alive()

    assert errors.empty()
    assert bus.is_alive() is False
    assert bus._shutdown_complete.is_set()


def test_executor_shutdown_failure_is_terminal_and_replayed_to_later_callers() -> None:
    bus = AudioEventBus()
    bus._enable_async = True
    bus._executor = FailingExecutor()

    with pytest.raises(EventBusShutdownError) as first:
        bus.shutdown()
    assert isinstance(first.value.__cause__, OSError)
    assert bus.is_alive() is False
    assert bus._shutdown_complete.is_set()

    with pytest.raises(EventBusShutdownError) as second:
        bus.shutdown()
    assert isinstance(second.value.__cause__, OSError)


def test_shutdown_is_rejected_from_active_synchronous_publish_callback() -> None:
    bus = AudioEventBus()

    def callback(data: object) -> None:
        bus.shutdown()

    bus.subscribe(AudioEventType.PLAYBACK_STARTED, callback)

    assert bus.publish(AudioEventType.PLAYBACK_STARTED) is True
    assert bus.is_alive() is True
    assert bus.get_stats()['errors'] == 1
    bus.shutdown()


def test_async_callback_can_initiate_shutdown_without_self_join_deadlock() -> None:
    bus = AudioEventBus(enable_async_processing=True, max_workers=1)
    callback_finished = threading.Event()

    def callback(data: object) -> None:
        bus.shutdown()
        callback_finished.set()

    bus.subscribe(AudioEventType.PLAYBACK_STARTED, callback)
    assert bus.publish(AudioEventType.PLAYBACK_STARTED) is True

    assert callback_finished.wait(timeout=2.0)
    assert bus._shutdown_complete.wait(timeout=2.0)
    assert bus.is_alive() is False


def test_terminal_bus_rejects_restart_and_mutating_configuration() -> None:
    bus = AudioEventBus()
    bus.shutdown()

    with pytest.raises(EventBusShutdownError):
        bus.start()
    with pytest.raises(EventBusShutdownError):
        bus.set_ui_dispatcher(lambda callback: callback())
    with pytest.raises(EventBusShutdownError):
        bus.set_rate_limit(AudioEventType.PLAYBACK_STARTED)
    with pytest.raises(EventBusShutdownError):
        bus.subscribe(AudioEventType.PLAYBACK_STARTED, lambda data: None)
    assert bus.publish(AudioEventType.PLAYBACK_STARTED) is False


def test_cross_thread_dispatch_callback_cannot_own_active_publish_shutdown() -> None:
    bus = AudioEventBus()
    callback_finished = threading.Event()

    def dispatcher(task: object) -> None:
        assert callable(task)

        def run() -> None:
            task()
            callback_finished.set()

        thread = threading.Thread(target=run)
        thread.start()
        assert callback_finished.wait(timeout=2.0)
        thread.join(timeout=2.0)
        assert not thread.is_alive()

    def callback(data: object) -> None:
        bus.shutdown()

    bus.set_ui_dispatcher(dispatcher)
    bus.subscribe(AudioEventType.PLAYBACK_STARTED, callback)

    assert bus.publish(AudioEventType.PLAYBACK_STARTED, require_ui_thread=True) is True
    assert bus.is_alive() is True
    assert bus.get_stats()['errors'] == 1
    bus.shutdown()


def test_async_callback_does_not_wait_on_concurrent_external_shutdown() -> None:
    bus = AudioEventBus(enable_async_processing=True, max_workers=1)
    callback_entered = threading.Event()
    invoke_nested_shutdown = threading.Event()
    nested_shutdown_returned = threading.Event()
    closer_errors: queue.Queue[Exception] = queue.Queue()

    def callback(data: object) -> None:
        callback_entered.set()
        assert invoke_nested_shutdown.wait(timeout=2.0)
        bus.shutdown()
        nested_shutdown_returned.set()

    bus.subscribe(AudioEventType.PLAYBACK_STARTED, callback)
    assert bus.publish(AudioEventType.PLAYBACK_STARTED) is True
    assert callback_entered.wait(timeout=2.0)

    def close() -> None:
        try:
            bus.shutdown()
        except Exception as error:
            closer_errors.put(error)

    closer = threading.Thread(target=close)
    closer.start()
    deadline = time.monotonic() + 2.0
    while not bus._shutting_down:
        if time.monotonic() >= deadline:
            raise AssertionError('external shutdown did not start')
        time.sleep(0.001)
    invoke_nested_shutdown.set()

    assert nested_shutdown_returned.wait(timeout=2.0)
    closer.join(timeout=2.0)
    assert not closer.is_alive()
    assert closer_errors.empty()
    assert bus._shutdown_complete.is_set()


def test_cleanup_failure_terminalizes_and_releases_later_shutdown_callers() -> None:
    class FailingRateLimit:
        def reset(self) -> None:
            raise OSError('rate-limit cleanup failed')

    bus = AudioEventBus()
    bus._rate_limits[AudioEventType.PLAYBACK_STARTED] = FailingRateLimit()

    with pytest.raises(EventBusShutdownError) as first:
        bus.shutdown()
    assert isinstance(first.value.__cause__, OSError)
    assert bus._shutdown_complete.is_set()

    with pytest.raises(EventBusShutdownError) as second:
        bus.shutdown()
    assert isinstance(second.value.__cause__, OSError)
