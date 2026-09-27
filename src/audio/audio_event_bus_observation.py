from __future__ import annotations

import logging
import math
import threading
from datetime import datetime
from typing import Callable

from .audio_event_models import (
    AudioEventType,
    EventBusShutdownError,
    EventMetadata,
    EventRecord,
    EventWaitConditionError,
    EventPayloadSnapshotError,
    snapshot_event_payload,
)

logger = logging.getLogger(__name__)

EventKey = AudioEventType | str
MAX_EVENT_HISTORY_SIZE = 10_000
MAX_EVENT_WAIT_SECONDS = 3_600.0
WAIT_SUBSCRIPTION_PRIORITY = 1_000_000
MAX_OBSERVATION_SNAPSHOT_ATTEMPTS = 16


class EventWaitState:
    """Coordinate one wait result without exposing mutable shared state."""

    __slots__ = ('_completed', '_error', '_event', '_lock', '_value')

    def __init__(self) -> None:
        self._completed = False
        self._error: Exception | None = None
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._value: object = None

    def is_open(self) -> bool:
        with self._lock:
            return not self._completed

    def complete(self, value: object) -> None:
        try:
            snapshot = snapshot_event_payload(value)
        except EventPayloadSnapshotError as error:
            self.fail(error)
            return
        with self._lock:
            if self._completed:
                return
            self._value = snapshot
            self._completed = True
            self._event.set()

    def fail(self, error: Exception) -> None:
        with self._lock:
            if self._completed:
                return
            self._error = error
            self._completed = True
            self._event.set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)

    def resolve(self) -> object | None:
        with self._lock:
            if not self._completed:
                self._completed = True
                return None
            error = self._error
            value = self._value
        if error is not None:
            raise error
        return value



def validate_history_size(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError('max_history_size must be an integer')
    if value < 0 or value > MAX_EVENT_HISTORY_SIZE:
        raise ValueError(
            f'max_history_size must be between 0 and {MAX_EVENT_HISTORY_SIZE}'
        )
    return value



def _validate_history_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError('history limit must be an integer')
    if value < 0 or value > MAX_EVENT_HISTORY_SIZE:
        raise ValueError(f'history limit must be between 0 and {MAX_EVENT_HISTORY_SIZE}')
    return value



def _validate_history_since(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError('history since must be a datetime or None')
    if value.tzinfo is not None and value.utcoffset() is not None:
        raise ValueError('history since must be a naive datetime')
    return value



def _validate_wait_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError('timeout must be a finite number')
    timeout = float(value)
    if not math.isfinite(timeout) or timeout < 0.0 or timeout > MAX_EVENT_WAIT_SECONDS:
        raise ValueError(f'timeout must be between 0 and {MAX_EVENT_WAIT_SECONDS} seconds')
    return timeout



def _event_key_value(event_type: EventKey) -> str:
    return event_type.value if isinstance(event_type, AudioEventType) else event_type



def _mark_observation_changed(self) -> None:
    self._observation_version += 1


def _snapshot_history(self) -> tuple[EventRecord, ...]:
    for _ in range(MAX_OBSERVATION_SNAPSHOT_ATTEMPTS):
        with self._lock:
            history = self._event_history
            version = self._history_version
        try:
            snapshot = tuple(history)
        except RuntimeError:
            continue
        with self._lock:
            if history is self._event_history and version == self._history_version:
                return snapshot
    # Slow path after contention: capture references, never clone payloads here.
    # OVERRIDE C18: O(history length), bounded by MAX_EVENT_HISTORY_SIZE.
    with self._lock:
        return tuple(self._event_history)


def _snapshot_observation_state(
    self,
) -> tuple[
    dict[str, object],
    tuple[tuple[EventKey, tuple[object, ...]], ...],
    dict[str, int],
    tuple[tuple[EventKey, object], ...],
    int,
    bool,
]:
    for _ in range(MAX_OBSERVATION_SNAPSHOT_ATTEMPTS):
        with self._lock:
            version = self._observation_version
            stats = dict(self._stats)
            subscribers = self._subscribers
            events_by_type = self._events_by_type
            rate_limits = self._rate_limits
            history_size = len(self._event_history)
            shutting_down = self._shutting_down
        try:
            subscriber_items = tuple(
                (key, tuple(items)) for key, items in subscribers.items()
            )
            event_counts = dict(events_by_type)
            rate_limit_items = tuple(rate_limits.items())
        except RuntimeError:
            continue
        with self._lock:
            stable = (
                version == self._observation_version
                and subscribers is self._subscribers
                and events_by_type is self._events_by_type
                and rate_limits is self._rate_limits
            )
        if stable:
            return (
                stats,
                subscriber_items,
                event_counts,
                rate_limit_items,
                history_size,
                shutting_down,
            )
    # OVERRIDE C18: one shallow structural capture; see event-bus-snapshots.md.
    # User properties, payload cloning and result formatting stay outside the lock.
    with self._lock:
        return (
            dict(self._stats),
            tuple((key, tuple(items)) for key, items in self._subscribers.items()),
            dict(self._events_by_type),
            tuple(self._rate_limits.items()),
            len(self._event_history),
            self._shutting_down,
        )


def _increment_integer_stat(self, name: str, amount: int = 1) -> None:
    current = self._stats[name]
    if isinstance(current, bool) or not isinstance(current, int):
        raise RuntimeError(f'Event bus stat {name!r} is not an integer')
    self._stats[name] = current + amount
    _mark_observation_changed(self)



def _clone_event_record(record: EventRecord) -> EventRecord:
    return EventRecord(
        event_type=record.event_type,
        data=snapshot_event_payload(record.data),
        metadata=record.metadata,
        subscribers_notified=record.subscribers_notified,
        processing_time_ms=record.processing_time_ms,
    )



def record_event(
    self,
    event_type: EventKey,
    history_data: object,
    metadata: EventMetadata,
    subscribers_notified: int,
    processing_time_ms: float,
) -> None:
    """Append one immutable event record and update synchronized counters.

    Edge cases:
        1. Payload aliases are removed before the record reaches this boundary.
        2. String and enum keys share one deterministic statistics representation.
        3. Corrupted integer counters fail closed instead of silently coercing values.
    """
    record = EventRecord(
        event_type=event_type,
        data=history_data,
        metadata=metadata,
        subscribers_notified=subscribers_notified,
        processing_time_ms=processing_time_ms,
    )
    key = _event_key_value(event_type)
    with self._lock:
        self._event_history.append(record)
        _increment_integer_stat(self, 'events_published')
        _increment_integer_stat(self, 'callbacks_dispatched', subscribers_notified)
        self._events_by_type[key] = self._events_by_type.get(key, 0) + 1
        self._history_version += 1



def get_event_history(
    self,
    event_type: EventKey | None = None,
    limit: int = 100,
    since: datetime | None = None,
) -> list[EventRecord]:
    validated_limit = _validate_history_limit(limit)
    validated_since = _validate_history_since(since)
    if validated_limit == 0:
        return []
    history = _snapshot_history(self)
    if event_type is not None:
        history = tuple(record for record in history if record.event_type == event_type)
    if validated_since is not None:
        history = tuple(
            record for record in history if record.metadata.timestamp > validated_since
        )
    return [_clone_event_record(record) for record in history[-validated_limit:]]



def clear_history(self) -> None:
    with self._lock:
        old_history = self._event_history
        self._event_history = type(old_history)(maxlen=self._max_history_size)
        self._history_version += 1
        _mark_observation_changed(self)
    del old_history



def get_stats(self) -> dict[str, object]:
    (
        stats,
        subscriber_items,
        events_by_type,
        rate_limit_items,
        history_size,
        shutting_down,
    ) = _snapshot_observation_state(self)
    start_time = stats['start_time']
    if not isinstance(start_time, datetime):
        raise RuntimeError('Event bus start_time stat is invalid')
    subscriptions_by_type: dict[str, int] = {}
    for event_type, subscriptions in subscriber_items:
        active_count = sum(1 for subscription in subscriptions if subscription.is_active)
        if active_count > 0:
            subscriptions_by_type[_event_key_value(event_type)] = active_count
    return {
        'uptime_seconds': (datetime.now() - start_time).total_seconds(),
        'events_published': stats['events_published'],
        'events_processed': stats['events_processed'],
        'callbacks_dispatched': stats['callbacks_dispatched'],
        'events_rate_limited': stats['events_rate_limited'],
        'subscriptions_active': sum(subscriptions_by_type.values()),
        'subscriptions_created': stats['subscriptions_created'],
        'subscriptions_removed': stats['subscriptions_removed'],
        'errors': stats['errors'],
        'history_size': history_size,
        'subscriptions_by_type': subscriptions_by_type,
        'events_by_type': events_by_type,
        'rate_limits': {
            _event_key_value(event_type): window.config
            for event_type, window in rate_limit_items
        },
        'shutting_down': shutting_down,
    }


def reset_stats(self) -> None:
    with self._lock:
        old_events_by_type = self._events_by_type
        self._stats = {
            'events_published': 0,
            'events_processed': 0,
            'callbacks_dispatched': 0,
            'events_rate_limited': 0,
            'subscriptions_created': 0,
            'subscriptions_removed': 0,
            'errors': 0,
            'start_time': datetime.now(),
        }
        self._events_by_type = {}
        _mark_observation_changed(self)
    del old_events_by_type



def _record_wait_condition_error(self, event_type: EventKey, error: Exception) -> None:
    logger.error('Event wait condition failed for %s: %s', event_type, error, exc_info=True)
    with self._lock:
        _increment_integer_stat(self, 'errors')



def _register_waiter(self, state: EventWaitState) -> None:
    with self._lock:
        if self._shutting_down:
            raise EventBusShutdownError('Cannot wait for an event while the bus is shutting down')
        self._event_waiters.add(state)



def _unregister_waiter(self, state: EventWaitState) -> None:
    with self._lock:
        self._event_waiters.discard(state)



def wait_for_event(
    self,
    event_type: EventKey,
    timeout: float = 10.0,
    condition: Callable[[object], bool] | None = None,
) -> object | None:
    """Wait for the first matching payload without consuming non-matches.

    Edge cases:
        1. Non-matching events leave the one-time waiter active.
        2. Condition failures wake the waiter and propagate a typed error.
        3. Shutdown cancels pending waiters instead of forcing a full timeout.
    """
    validated_timeout = _validate_wait_timeout(timeout)
    if condition is not None and not callable(condition):
        raise TypeError('condition must be callable or None')
    state = EventWaitState()
    _register_waiter(self, state)

    def filter_condition(data: object) -> bool:
        if not state.is_open():
            return False
        if condition is None:
            return True
        try:
            accepted = bool(condition(data))
        except Exception as error:
            _record_wait_condition_error(self, event_type, error)
            wait_error = EventWaitConditionError('Event wait condition failed')
            wait_error.__cause__ = error
            state.fail(wait_error)
            return False
        return accepted and state.is_open()

    subscription = None
    try:
        subscription = self.subscribe(
            event_type=event_type,
            callback=state.complete,
            priority=WAIT_SUBSCRIPTION_PRIORITY,
            filter_condition=filter_condition,
            one_time=True,
        )
        state.wait(validated_timeout)
        return state.resolve()
    finally:
        if subscription is not None:
            self.unsubscribe(event_type, subscription=subscription)
        _unregister_waiter(self, state)



def get_active_subscriptions(self) -> dict[EventKey, list[str]]:
    _, subscriber_items, _, _, _, _ = _snapshot_observation_state(self)
    result: dict[EventKey, list[str]] = {}
    for event_type, subscriptions in subscriber_items:
        active_ids = [
            subscription.subscription_id
            for subscription in subscriptions
            if subscription.is_active
        ]
        if active_ids:
            result[event_type] = active_ids
    return result
