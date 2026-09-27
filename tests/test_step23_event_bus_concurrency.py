from __future__ import annotations

import logging
import queue
import threading
import time

import pytest

from src.audio import audio_event_bus_publish as publish_mod
from src.audio.audio_event_bus import AudioEventBus
from src.audio.audio_event_models import AudioEventType


class SubscriberFailure(Exception):
    pass


def _publish_from_threads(
    bus: AudioEventBus,
    event_type: AudioEventType,
    workers: int,
) -> list[bool]:
    barrier = threading.Barrier(workers)
    results: queue.Queue[bool] = queue.Queue()

    def worker(index: int) -> None:
        barrier.wait(timeout=5.0)
        results.put(bus.publish(event_type, {'index': index}))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)
        assert not thread.is_alive()
    return [results.get_nowait() for _ in range(workers)]


def _assert_bus_lock_available_from_another_thread(bus: AudioEventBus) -> None:
    acquired = threading.Event()

    def reader() -> None:
        bus.get_stats()
        acquired.set()

    thread = threading.Thread(target=reader)
    thread.start()
    assert acquired.wait(timeout=2.0)
    thread.join(timeout=2.0)
    assert not thread.is_alive()


@pytest.mark.parametrize('workers', [2, 4, 8, 16, 32, 64])
def test_rate_limit_reservation_is_atomic_without_subscribers(workers: int) -> None:
    bus = AudioEventBus(max_history_size=workers)
    event_type = AudioEventType.PLAYBACK_PROGRESS
    bus.set_rate_limit(event_type, min_interval_seconds=60.0, max_per_interval=1)

    results = _publish_from_threads(bus, event_type, workers)
    stats = bus.get_stats()

    assert results.count(True) == 1
    assert results.count(False) == workers - 1
    assert stats['events_published'] == 1
    assert stats['events_rate_limited'] == workers - 1
    assert stats['history_size'] == 1
    assert bus._rate_limits[event_type].used == 1


def test_rate_limit_window_expires_in_constant_work(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(publish_mod.time, 'monotonic', lambda: clock[0])
    bus = AudioEventBus(max_history_size=16)
    event_type = AudioEventType.PLAYBACK_PROGRESS
    bus.set_rate_limit(event_type, min_interval_seconds=1.0, max_per_interval=3)

    assert [bus.publish(event_type) for _ in range(4)] == [True, True, True, False]
    window = bus._rate_limits[event_type]
    assert window.used == 3
    assert window.capacity == 3
    assert len(window._timestamps) == 3

    clock[0] = 101.0
    assert [bus.publish(event_type) for _ in range(4)] == [True, True, True, False]
    assert window.used == 3
    assert bus.get_stats()['events_rate_limited'] == 2


def test_rate_limit_clock_regression_fails_closed(monkeypatch) -> None:
    clock = [10.0]
    monkeypatch.setattr(publish_mod.time, 'monotonic', lambda: clock[0])
    bus = AudioEventBus()
    event_type = AudioEventType.PLAYBACK_PROGRESS
    bus.set_rate_limit(event_type, min_interval_seconds=1.0, max_per_interval=1)

    assert bus.publish(event_type) is True
    clock[0] = 9.0
    assert bus.publish(event_type) is False
    clock[0] = 11.0
    assert bus.publish(event_type) is True


def test_clear_rate_limit_removes_prior_reservations() -> None:
    bus = AudioEventBus()
    event_type = AudioEventType.PLAYBACK_PROGRESS
    bus.set_rate_limit(event_type, min_interval_seconds=60.0, max_per_interval=1)

    assert bus.publish(event_type) is True
    assert bus.publish(event_type) is False
    bus.clear_rate_limit(event_type)
    assert bus.publish(event_type) is True


def test_filter_callback_and_dispatcher_run_outside_central_lock() -> None:
    bus = AudioEventBus()
    event_type = AudioEventType.PLAYBACK_STARTED
    callback_calls: list[object] = []

    def filter_condition(data: object) -> bool:
        _assert_bus_lock_available_from_another_thread(bus)
        return True

    def callback(data: object) -> None:
        _assert_bus_lock_available_from_another_thread(bus)
        callback_calls.append(data)

    def dispatcher(dispatched_callback) -> None:
        _assert_bus_lock_available_from_another_thread(bus)
        dispatched_callback()

    bus.set_ui_dispatcher(dispatcher)
    bus.subscribe(event_type, callback, filter_condition=filter_condition)

    assert bus.publish(event_type, {'ok': True}, require_ui_thread=True) is True
    assert callback_calls == [{'ok': True}]
    assert bus.get_stats()['errors'] == 0



def test_filter_that_unsubscribes_itself_cannot_dispatch_callback() -> None:
    bus = AudioEventBus()
    event_type = AudioEventType.PLAYBACK_STARTED
    callback_calls: list[object] = []
    holder: list[object] = []

    def filter_condition(data: object) -> bool:
        assert bus.unsubscribe(event_type, subscription=holder[0]) is True
        return True

    holder.append(
        bus.subscribe(event_type, callback_calls.append, filter_condition=filter_condition)
    )

    assert bus.publish(event_type, {'ok': True}) is True
    assert callback_calls == []
    assert bus.get_subscriber_count(event_type) == 0

def test_concurrent_callback_failures_never_escape_or_lose_error_counts(caplog) -> None:
    caplog.set_level(logging.CRITICAL)
    workers = 64
    bus = AudioEventBus(max_history_size=workers)
    event_type = AudioEventType.PLAYBACK_STARTED

    def failing_callback(data: object) -> None:
        raise SubscriberFailure('expected failure')

    subscription = bus.subscribe(event_type, failing_callback)
    results = _publish_from_threads(bus, event_type, workers)
    stats = bus.get_stats()

    assert results == [True] * workers
    assert stats['events_published'] == workers
    assert stats['events_processed'] == 0
    assert stats['errors'] == workers
    assert subscription.call_count == 0


def test_concurrent_successful_publishes_do_not_lose_stats_updates() -> None:
    workers = 32
    publishes_per_worker = 40
    bus = AudioEventBus(max_history_size=workers * publishes_per_worker)
    event_type = AudioEventType.PLAYBACK_PROGRESS
    barrier = threading.Barrier(workers)

    def worker(worker_id: int) -> None:
        barrier.wait(timeout=5.0)
        for sequence in range(publishes_per_worker):
            assert bus.publish(event_type, (worker_id, sequence)) is True

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15.0)
        assert not thread.is_alive()

    expected = workers * publishes_per_worker
    stats = bus.get_stats()
    assert stats['events_published'] == expected
    assert stats['events_processed'] == 0
    assert stats['history_size'] == expected
    assert bus._event_counter == expected


def test_async_callback_failure_is_counted_once(caplog) -> None:
    caplog.set_level(logging.CRITICAL)
    bus = AudioEventBus(enable_async_processing=True, max_workers=2)
    event_type = AudioEventType.PLAYBACK_STARTED
    callback_entered = threading.Event()

    def failing_callback(data: object) -> None:
        callback_entered.set()
        raise SubscriberFailure('async failure')

    subscription = bus.subscribe(event_type, failing_callback)
    executor = bus._executor
    assert executor is not None
    assert bus.publish(event_type, {'async': True}) is True
    assert callback_entered.wait(timeout=2.0)

    deadline = time.monotonic() + 2.0
    while bus.get_stats()['errors'] != 1 and time.monotonic() < deadline:
        time.sleep(0.001)
    executor.shutdown(wait=True)
    bus._executor = None

    stats = bus.get_stats()
    assert stats['errors'] == 1
    assert stats['events_published'] == 1
    assert stats['events_processed'] == 0
    assert stats['callbacks_dispatched'] == 1
    assert subscription.call_count == 0


def test_string_event_key_stats_match_native_video_publishers() -> None:
    bus = AudioEventBus()

    assert bus.publish('VIDEO_READY', {'event': 1}) is True
    assert bus.publish('VIDEO_READY', {'event': 2}) is True

    stats = bus.get_stats()
    assert stats['events_by_type'] == {'VIDEO_READY': 2}
