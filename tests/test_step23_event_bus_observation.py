from __future__ import annotations

import threading
import time
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from src.audio.audio_event_bus import AudioEventBus
from src.audio import audio_event_bus_observation as observation_mod
from src.audio.audio_event_bus_observation import MAX_EVENT_HISTORY_SIZE
from src.audio.audio_event_models import (
    AudioEventType,
    EventPayloadSnapshotError,
    EventWaitConditionError,
    MAX_EVENT_CONTAINER_ITEMS,
)


def _wait_for_subscriber(bus: AudioEventBus, event_type: AudioEventType) -> None:
    deadline = time.monotonic() + 2.0
    while bus.get_subscriber_count(event_type) == 0:
        if time.monotonic() >= deadline:
            raise AssertionError('waiter subscription was not registered')
        time.sleep(0.001)


def test_wait_ignores_non_matching_event_and_returns_first_match() -> None:
    bus = AudioEventBus()
    event_type = AudioEventType.PLAYBACK_STARTED
    result: list[object | None] = []

    thread = threading.Thread(
        target=lambda: result.append(
            bus.wait_for_event(event_type, 1.0, lambda data: data == {'match': True})
        )
    )
    thread.start()
    _wait_for_subscriber(bus, event_type)

    assert bus.publish(event_type, {'match': False}) is True
    assert bus.get_subscriber_count(event_type) == 1
    assert bus.publish(event_type, {'match': True}) is True

    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert result == [{'match': True}]
    assert bus.get_subscriber_count(event_type) == 0


def test_wait_condition_failure_wakes_caller_with_original_cause() -> None:
    bus = AudioEventBus()
    event_type = AudioEventType.PLAYBACK_STARTED
    errors: list[Exception] = []

    def wait() -> None:
        try:
            bus.wait_for_event(
                event_type,
                1.0,
                lambda data: (_ for _ in ()).throw(KeyError('condition failed')),
            )
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=wait)
    thread.start()
    _wait_for_subscriber(bus, event_type)
    assert bus.publish(event_type, {'value': 1}) is True
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], EventWaitConditionError)
    assert isinstance(errors[0].__cause__, KeyError)
    assert bus.get_subscriber_count(event_type) == 0
    assert bus.get_stats()['errors'] == 1


def test_one_time_delivery_is_atomic_under_concurrent_publishers() -> None:
    workers = 32
    bus = AudioEventBus(max_history_size=workers)
    event_type = AudioEventType.PLAYBACK_STARTED
    callback_values: list[int] = []
    callback_lock = threading.Lock()
    barrier = threading.Barrier(workers)

    def callback(data: object) -> None:
        assert isinstance(data, dict)
        with callback_lock:
            callback_values.append(int(data['index']))

    subscription = bus.subscribe(event_type, callback, one_time=True)

    def publish(index: int) -> None:
        barrier.wait(timeout=5.0)
        assert bus.publish(event_type, {'index': index}) is True

    threads = [threading.Thread(target=publish, args=(index,)) for index in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)
        assert not thread.is_alive()

    assert len(callback_values) == 1
    assert subscription.call_count == 1
    assert bus.get_subscriber_count(event_type) == 0
    stats = bus.get_stats()
    assert stats['callbacks_dispatched'] == 1
    assert stats['events_processed'] == 1


def test_history_snapshots_published_payload_before_callback_mutation() -> None:
    bus = AudioEventBus()
    event_type = AudioEventType.PLAYBACK_STARTED
    payload = {'nested': [1]}

    def mutate(data: object) -> None:
        assert isinstance(data, dict)
        data['nested'].append(2)

    bus.subscribe(event_type, mutate)
    assert bus.publish(event_type, payload) is True

    assert payload == {'nested': [1, 2]}
    assert bus.get_event_history()[0].data == {'nested': [1]}


def test_history_results_cannot_mutate_internal_records() -> None:
    bus = AudioEventBus()
    assert bus.publish(AudioEventType.PLAYBACK_STARTED, {'nested': [1]}) is True

    first = bus.get_event_history()[0]
    assert isinstance(first.data, dict)
    first.data['nested'].append(2)
    with pytest.raises(FrozenInstanceError):
        first.subscribers_notified = 99

    second = bus.get_event_history()[0]
    assert second.data == {'nested': [1]}
    assert second.subscribers_notified == 0


def test_reset_stats_preserves_monotonic_event_ids_and_resets_type_counts() -> None:
    bus = AudioEventBus()
    assert bus.publish(AudioEventType.PLAYBACK_STARTED) is True
    first_id = bus.get_event_history()[0].metadata.event_id

    bus.reset_stats()
    assert bus.publish(AudioEventType.PLAYBACK_STOPPED) is True

    history = bus.get_event_history()
    assert [record.metadata.event_id for record in history] == [first_id, 'evt_2']
    stats = bus.get_stats()
    assert stats['events_published'] == 1
    assert stats['events_by_type'] == {'playback_stopped': 1}
    assert stats['history_size'] == 2


def test_history_query_validates_limits_and_datetime_domain() -> None:
    bus = AudioEventBus()
    assert bus.publish(AudioEventType.PLAYBACK_STARTED) is True

    assert bus.get_event_history(limit=0) == []
    for value in (True, -1, MAX_EVENT_HISTORY_SIZE + 1):
        with pytest.raises((TypeError, ValueError)):
            bus.get_event_history(limit=value)
    with pytest.raises(TypeError):
        bus.get_event_history(since='2026-01-01')
    with pytest.raises(ValueError, match='naive datetime'):
        bus.get_event_history(since=datetime.now(UTC))


def test_payload_snapshot_rejects_cycles_oversize_and_unsupported_objects() -> None:
    class Unsupported:
        pass

    payloads: list[object] = [Unsupported(), list(range(MAX_EVENT_CONTAINER_ITEMS + 1))]
    cyclic: list[object] = []
    cyclic.append(cyclic)
    payloads.append(cyclic)

    for payload in payloads:
        bus = AudioEventBus()
        callback_calls: list[object] = []
        bus.subscribe(AudioEventType.PLAYBACK_STARTED, callback_calls.append)

        assert bus.publish(AudioEventType.PLAYBACK_STARTED, payload) is False
        assert callback_calls == []
        assert bus.get_event_history() == []
        assert bus.get_stats()['errors'] == 1


def test_event_bus_constructor_rejects_unbounded_or_ambiguous_configuration() -> None:
    for value in (True, -1, MAX_EVENT_HISTORY_SIZE + 1):
        with pytest.raises((TypeError, ValueError)):
            AudioEventBus(max_history_size=value)
    for value in (0, 65, True):
        with pytest.raises((TypeError, ValueError)):
            AudioEventBus(max_workers=value)
    with pytest.raises(TypeError):
        AudioEventBus(enable_async_processing=1)


def test_wait_timeout_validation_is_bounded_and_unsubscribes() -> None:
    bus = AudioEventBus()
    event_type = AudioEventType.PLAYBACK_STOPPED

    assert bus.wait_for_event(event_type, timeout=0.0) is None
    assert bus.get_subscriber_count(event_type) == 0
    for value in (True, -1.0, float('inf'), 3600.1):
        with pytest.raises((TypeError, ValueError)):
            bus.wait_for_event(event_type, timeout=value)


def test_history_payload_cloning_runs_outside_the_central_lock(monkeypatch) -> None:
    bus = AudioEventBus()
    assert bus.publish(AudioEventType.PLAYBACK_STARTED, {'value': 1}) is True
    original_snapshot = observation_mod.snapshot_event_payload
    lock_was_available = threading.Event()

    def probe_snapshot(value: object) -> object:
        completed = threading.Event()

        def acquire_lock() -> None:
            with bus._lock:
                lock_was_available.set()
            completed.set()

        thread = threading.Thread(target=acquire_lock)
        thread.start()
        assert completed.wait(timeout=2.0)
        thread.join(timeout=2.0)
        assert not thread.is_alive()
        return original_snapshot(value)

    monkeypatch.setattr(observation_mod, 'snapshot_event_payload', probe_snapshot)
    assert bus.get_event_history()[0].data == {'value': 1}
    assert lock_was_available.is_set()


def test_subscription_stat_scan_runs_outside_the_central_lock() -> None:
    bus = AudioEventBus()
    lock_was_available = threading.Event()

    class ProbeSubscription:
        subscription_id = 'probe-subscription'

        @property
        def is_active(self) -> bool:
            completed = threading.Event()

            def acquire_lock() -> None:
                with bus._lock:
                    lock_was_available.set()
                completed.set()

            thread = threading.Thread(target=acquire_lock)
            thread.start()
            assert completed.wait(timeout=2.0)
            thread.join(timeout=2.0)
            assert not thread.is_alive()
            return True

    with bus._lock:
        bus._subscribers[AudioEventType.PLAYBACK_STARTED] = [ProbeSubscription()]
        bus._observation_version += 1

    stats = bus.get_stats()
    assert stats['subscriptions_active'] == 1
    assert lock_was_available.is_set()


def test_history_and_stats_snapshots_converge_under_concurrent_publish_churn() -> None:
    bus = AudioEventBus(max_history_size=512)
    errors: list[Exception] = []
    start = threading.Barrier(3)

    def publish_events() -> None:
        try:
            start.wait(timeout=5.0)
            for index in range(2_000):
                assert bus.publish(AudioEventType.PLAYBACK_PROGRESS, {'index': index}) is True
        except Exception as error:
            errors.append(error)

    def read_snapshots() -> None:
        try:
            start.wait(timeout=5.0)
            for _ in range(1_000):
                bus.get_event_history(limit=64)
                bus.get_stats()
        except Exception as error:
            errors.append(error)

    publisher = threading.Thread(target=publish_events)
    reader = threading.Thread(target=read_snapshots)
    publisher.start()
    reader.start()
    start.wait(timeout=5.0)
    publisher.join(timeout=10.0)
    reader.join(timeout=10.0)

    assert not publisher.is_alive()
    assert not reader.is_alive()
    assert errors == []
    assert bus.get_stats()['events_published'] == 2_000
    assert len(bus.get_event_history(limit=512)) == 512
