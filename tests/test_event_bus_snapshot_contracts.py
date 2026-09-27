"""Snapshot slow-path ownership, failure and concurrent-consumer contracts."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from src.audio.audio_event_bus import AudioEventBus
from src.audio import audio_event_bus_observation as observation
from src.audio.audio_event_rate_limit import RateLimitWindow


@pytest.fixture
def slow_bus(monkeypatch):
    # Force only the slow-path branch; the separate liveness tests exercise all 16 attempts.
    monkeypatch.setattr(observation, 'MAX_OBSERVATION_SNAPSHOT_ATTEMPTS', 0)
    bus = AudioEventBus(max_history_size=512)
    try:
        yield bus
    finally:
        bus.shutdown()


@pytest.mark.parametrize('capacity', [0, 1, 512, 10_000])
def test_slow_history_retains_bounded_order_and_counts(monkeypatch, capacity):
    monkeypatch.setattr(observation, 'MAX_OBSERVATION_SNAPSHOT_ATTEMPTS', 0)
    bus = AudioEventBus(max_history_size=capacity)
    try:
        for index in range(capacity + 2):
            assert bus.publish('history', {'index': index})
        records = bus.get_event_history(limit=10_000)
        assert [record.data['index'] for record in records] == list(range(2, capacity+2))
        stats = bus.get_stats()
        assert stats['history_size'] == capacity
        assert stats['events_published'] == capacity+2
        assert stats['events_by_type'] == {'history': capacity+2}
    finally:
        bus.shutdown()


def test_slow_history_filters_and_limits_preserve_order(slow_bus):
    before = datetime.now() - timedelta(days=1)
    for index in range(8):
        assert slow_bus.publish('even' if index % 2 == 0 else 'odd', {'index': index})
    assert [r.data['index'] for r in slow_bus.get_event_history('even', 2, before)] == [4, 6]
    assert slow_bus.get_event_history(limit=0) == []
    assert slow_bus.get_event_history(since=datetime.now()+timedelta(days=1)) == []


def test_slow_history_clones_payloads_after_unlock(monkeypatch, slow_bus):
    assert slow_bus.publish('history', {'nested': [1]})
    original = observation.snapshot_event_payload
    acquisitions = []

    def clone(value):
        done = threading.Event()
        def acquire():
            with slow_bus._lock:
                acquisitions.append(True)
            done.set()
        thread = threading.Thread(target=acquire)
        thread.start()
        assert done.wait(2.0)
        thread.join(2.0)
        assert not thread.is_alive()
        return original(value)

    monkeypatch.setattr(observation, 'snapshot_event_payload', clone)
    first = slow_bus.get_event_history()
    first[0].data['nested'].append(9)
    assert slow_bus.get_event_history()[0].data == {'nested': [1]}
    assert acquisitions == [True, True]


@pytest.mark.parametrize('reader', ['get_stats', 'get_active_subscriptions'])
def test_slow_subscription_properties_run_after_unlock(slow_bus, reader):
    acquisitions = []

    class Subscription:
        subscription_id = 'observed'

        @property
        def is_active(self):
            done = threading.Event()
            def acquire():
                with slow_bus._lock:
                    acquisitions.append(True)
                done.set()
            thread = threading.Thread(target=acquire)
            thread.start()
            assert done.wait(2.0)
            thread.join(2.0)
            assert not thread.is_alive()
            return True

        def deactivate(self):
            return None

    with slow_bus._lock:
        slow_bus._subscribers['event'] = [Subscription()]
    result = getattr(slow_bus, reader)()
    assert acquisitions == [True]
    assert result['subscriptions_active'] == 1 if reader == 'get_stats' else result == {'event': ['observed']}


def test_slow_snapshots_are_detached_from_later_container_replacement(slow_bus):
    assert slow_bus.publish('event', {'value': [1]})
    slow_bus.subscribe('event', lambda data: None, subscription_id='first')
    stats = slow_bus.get_stats()
    active = slow_bus.get_active_subscriptions()
    history = slow_bus.get_event_history()
    slow_bus.clear_history()
    slow_bus.reset_stats()
    slow_bus.unsubscribe_all()
    assert stats['events_published'] == 1
    assert stats['events_by_type'] == {'event': 1}
    assert active == {'event': ['first']}
    assert history[0].data == {'value': [1]}
    stats['events_by_type']['event'] = 100
    active['event'].append('fake')
    assert slow_bus.get_stats()['events_by_type'] == {}
    assert slow_bus.get_active_subscriptions() == {}


@pytest.mark.parametrize('error_cls', [RuntimeError, MemoryError, KeyboardInterrupt, SystemExit])
def test_slow_capture_errors_propagate_and_release_lock(slow_bus, error_cls):
    expected = error_cls('controlled-copy-error')
    original = slow_bus._event_history

    class BrokenHistory(deque):
        def __iter__(self):
            raise expected

    slow_bus._event_history = BrokenHistory(maxlen=512)
    try:
        with pytest.raises(error_cls) as caught:
            slow_bus.get_event_history()
        assert caught.value is expected
        acquired = threading.Event()
        def probe():
            with slow_bus._lock:
                acquired.set()
        thread = threading.Thread(target=probe)
        thread.start()
        assert acquired.wait(2.0)
        thread.join(2.0)
        assert not thread.is_alive()
    finally:
        slow_bus._event_history = original


@pytest.mark.parametrize('error_cls', [ValueError, MemoryError, KeyboardInterrupt, SystemExit])
def test_payload_clone_errors_are_not_converted_to_empty_success(monkeypatch, slow_bus, error_cls):
    assert slow_bus.publish('event', {'value': 1})
    expected = error_cls('controlled-clone-error')
    def fail(value):
        raise expected
    monkeypatch.setattr(observation, 'snapshot_event_payload', fail)
    with pytest.raises(error_cls) as caught:
        slow_bus.get_event_history()
    assert caught.value is expected


def test_shutdown_and_reset_keep_existing_history_contract(slow_bus):
    assert slow_bus.publish('event', 1)
    slow_bus.reset_stats()
    assert slow_bus.get_stats()['events_published'] == 0
    assert len(slow_bus.get_event_history()) == 1
    slow_bus.shutdown()
    assert slow_bus.get_stats()['shutting_down'] is True
    assert slow_bus.get_event_history() == []
    assert slow_bus.publish('event', 2) is False


@pytest.mark.parametrize('force_slow', [False, True])
def test_concurrent_readers_and_publishers_observe_real_consistent_counters(monkeypatch, force_slow):
    if force_slow:
        monkeypatch.setattr(observation, 'MAX_OBSERVATION_SNAPSHOT_ATTEMPTS', 0)
    bus = AudioEventBus(max_history_size=512)
    start = threading.Barrier(6)
    totals = []

    def publisher(worker):
        start.wait(timeout=5.0)
        for index in range(400):
            assert bus.publish(f'worker-{worker}', {'worker': worker, 'index': index})

    def reader():
        start.wait(timeout=5.0)
        for _ in range(300):
            records = bus.get_event_history(limit=32)
            assert len(records) <= 32
            assert all(0 <= r.data['index'] < 400 for r in records)
            stats = bus.get_stats()
            assert sum(stats['events_by_type'].values()) == stats['events_published']
            assert stats['history_size'] == min(stats['events_published'], 512)
            totals.append(stats['events_published'])

    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(publisher, i) for i in range(4)]
            futures += [pool.submit(reader) for _ in range(2)]
            for future in futures:
                future.result(timeout=15.0)
        assert bus.get_stats()['events_published'] == 1600
        assert len(bus.get_event_history(limit=512)) == 512
        assert len(totals) == 600
    finally:
        bus.shutdown()


def test_slow_copy_keeps_the_existing_lock_and_unmodified_writer_protocol(slow_bus):
    lock = slow_bus._lock
    assert slow_bus._publish_condition._lock is lock
    for _ in range(10):
        slow_bus.get_event_history()
        slow_bus.get_stats()
        slow_bus.get_active_subscriptions()
    assert slow_bus._lock is lock
    assert slow_bus._publish_condition._lock is lock


def test_snapshot_from_callback_can_reenter_without_deadlock(slow_bus):
    observed = []
    def callback(data):
        observed.append((slow_bus.get_event_history(), slow_bus.get_stats()))
    slow_bus.subscribe('event', callback)
    assert slow_bus.publish('event', {'value': 1})
    assert len(observed) == 1
    assert observed[0][0] == []
    assert observed[0][1]['events_published'] == 0
    assert slow_bus.get_stats()['events_published'] == 1


def test_slow_payload_cloning_allows_a_real_publisher_to_progress(monkeypatch, slow_bus):
    assert slow_bus.publish('event', {'value': 1})
    original = observation.snapshot_event_payload
    publication = []

    def clone(value):
        def publish():
            publication.append(slow_bus.publish('concurrent', {'value': 2}))
        thread = threading.Thread(target=publish)
        thread.start()
        thread.join(2.0)
        assert not thread.is_alive()
        return original(value)

    monkeypatch.setattr(observation, 'snapshot_event_payload', clone)
    records = slow_bus.get_event_history()
    assert [record.event_type for record in records] == ['event']
    assert publication == [True]
    assert slow_bus.get_stats()['events_published'] == 2


def test_slow_stats_config_conversion_stays_outside_lock(slow_bus):
    reached = threading.Event()
    class Window(RateLimitWindow):
        @property
        def config(self):
            def probe():
                with slow_bus._lock:
                    reached.set()
            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(2.0)
            assert not thread.is_alive()
            return (0.5, 2)
    with slow_bus._lock:
        slow_bus._rate_limits['event'] = Window(0.5, 2)
    assert slow_bus.get_stats()['rate_limits'] == {'event': (0.5, 2)}
    assert reached.is_set()


@pytest.mark.parametrize('reader', ['get_stats', 'get_active_subscriptions'])
def test_slow_subscription_errors_propagate(reader, slow_bus):
    error = ValueError('subscription-property-error')
    class Subscription:
        @property
        def is_active(self):
            raise error
        def deactivate(self):
            return None
    with slow_bus._lock:
        slow_bus._subscribers['event'] = [Subscription()]
    with pytest.raises(ValueError) as caught:
        getattr(slow_bus, reader)()
    assert caught.value is error


def test_fallback_does_not_change_original_attempt_budget() -> None:
    assert observation.MAX_OBSERVATION_SNAPSHOT_ATTEMPTS == 16
