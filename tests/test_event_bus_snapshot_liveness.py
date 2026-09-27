"""Regression tests for snapshot liveness with real, deterministically scheduled writers."""
from __future__ import annotations

from contextlib import contextmanager
import inspect
from queue import Queue
import sys
import threading

import pytest

from src.audio.audio_event_bus import AudioEventBus
from src.audio import audio_event_bus_observation as observation


@contextmanager
def force_interleavings(target, action):
    """Run an actual writer after each optimistic capture, not while its lock is held."""
    lines, first = inspect.getsourcelines(target)
    trigger = first + next(i for i, line in enumerate(lines) if line.strip() == 'try:')
    requests: Queue[int | None] = Queue(maxsize=1)
    replies: Queue[BaseException | None] = Queue(maxsize=1)
    counts = [0]
    previous_trace = sys.gettrace()

    def writer() -> None:
        while True:
            item = requests.get(timeout=5.0)
            if item is None:
                return
            try:
                action(item)
            except BaseException as error:
                # Test boundary: return the exact failure to the reader; never call it success.
                replies.put(error, timeout=5.0)
            else:
                replies.put(None, timeout=5.0)

    thread = threading.Thread(target=writer, name='snapshot-regression-writer')
    thread.start()

    def trace(frame, event, arg):
        if (event == 'line' and frame.f_code is target.__code__
                and frame.f_lineno == trigger):
            counts[0] += 1
            requests.put(counts[0], timeout=5.0)
            error = replies.get(timeout=5.0)
            if error is not None:
                raise AssertionError('controlled writer failed') from error
        return trace

    sys.settrace(trace)
    try:
        yield counts
    finally:
        sys.settrace(previous_trace)
        requests.put(None, timeout=5.0)
        thread.join(timeout=5.0)
        assert not thread.is_alive()


def _publish(bus, index):
    assert bus.publish('snapshot-event', {'index': index})


@pytest.mark.parametrize('capacity', [0, 1, 8, 512])
def test_history_read_survives_every_optimistic_attempt_invalidated(capacity) -> None:
    bus = AudioEventBus(max_history_size=capacity)
    try:
        with force_interleavings(
            observation._snapshot_history, lambda i: _publish(bus, i)
        ) as counts:
            records = bus.get_event_history(limit=512)
        assert counts == [16]
        expected = list(range(max(1, 17-capacity), 17))
        assert [record.data['index'] for record in records] == expected
        assert bus.get_stats()['events_published'] == 16
    finally:
        bus.shutdown()


@pytest.mark.parametrize('operation', ['stats', 'subscriptions'])
def test_observation_survives_publication_at_all_sixteen_captures(operation) -> None:
    bus = AudioEventBus(max_history_size=8)
    subscription = bus.subscribe('snapshot-event', lambda data: None, subscription_id='kept')
    try:
        getter = bus.get_stats if operation == 'stats' else bus.get_active_subscriptions
        with force_interleavings(
            observation._snapshot_observation_state, lambda i: _publish(bus, i)
        ) as counts:
            result = getter()
        assert counts == [16]
        if operation == 'stats':
            assert result['events_published'] == 16
            assert result['events_by_type'] == {'snapshot-event': 16}
            assert result['history_size'] == 8
            assert result['subscriptions_active'] == 1
        else:
            assert result == {'snapshot-event': ['kept']}
        assert subscription.call_count == 16
    finally:
        bus.shutdown()


@pytest.mark.parametrize('mutation', ['clear', 'reset', 'subscribe', 'rate', 'shutdown'])
def test_observation_handles_structural_replacement_and_lifecycle(mutation) -> None:
    bus = AudioEventBus(max_history_size=8)

    def change(index):
        if mutation == 'clear':
            bus.clear_history()
            _publish(bus, index)
        elif mutation == 'reset':
            bus.reset_stats()
            _publish(bus, index)
        elif mutation == 'subscribe':
            bus.subscribe('snapshot-event', lambda data: None, subscription_id=f'sub-{index}')
        elif mutation == 'rate':
            bus.set_rate_limit(f'event-{index}', min_interval_seconds=1.0, max_per_interval=2)
        else:
            bus.shutdown()

    try:
        with force_interleavings(observation._snapshot_observation_state, change) as counts:
            stats = bus.get_stats()
        assert counts[0] >= 1
        if mutation != 'shutdown':
            assert counts == [16]
        if mutation == 'clear':
            assert stats['events_published'] == 16
            assert stats['history_size'] == 1
        elif mutation == 'reset':
            assert stats['events_published'] == 1
            assert stats['events_by_type'] == {'snapshot-event': 1}
        elif mutation == 'subscribe':
            assert stats['subscriptions_active'] == 16
        elif mutation == 'rate':
            assert len(stats['rate_limits']) == 16
        else:
            assert stats['shutting_down'] is True
    finally:
        bus.shutdown()


def test_history_clear_during_capture_returns_real_current_records() -> None:
    bus = AudioEventBus(max_history_size=8)

    def change(index):
        bus.clear_history()
        _publish(bus, index)

    try:
        with force_interleavings(observation._snapshot_history, change) as counts:
            result = bus.get_event_history()
        assert counts == [16]
        assert len(result) == 1
        assert result[0].data == {'index': 16}
    finally:
        bus.shutdown()
