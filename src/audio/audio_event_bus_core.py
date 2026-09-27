from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from typing import Callable

from .audio_event_bus_observation import EventWaitState, _mark_observation_changed
from .audio_event_models import (
    AudioEventType,
    EventBusShutdownError,
    EventRecord,
    EventSubscription,
)
from .audio_event_rate_limit import RateLimitWindow

EventKey = AudioEventType | str
MAX_EVENT_KEY_LENGTH = 128
MAX_SUBSCRIPTION_ID_LENGTH = 256
MAX_SUBSCRIPTION_PRIORITY = 1_000_000

logger = logging.getLogger(__name__)


def _validate_event_key(value: object) -> EventKey:
    if isinstance(value, AudioEventType):
        return value
    if not isinstance(value, str):
        raise TypeError('event_type must be an AudioEventType or string')
    if not value or len(value) > MAX_EVENT_KEY_LENGTH or any(ord(char) < 32 for char in value):
        raise ValueError('string event_type must be non-empty, bounded, and contain no controls')
    return value


def _validate_priority(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError('priority must be an integer')
    if abs(value) > MAX_SUBSCRIPTION_PRIORITY:
        raise ValueError('priority exceeds the supported range')
    return value


def _validate_subscription_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError('subscription_id must be a string or None')
    if not value or len(value) > MAX_SUBSCRIPTION_ID_LENGTH:
        raise ValueError('subscription_id must be non-empty and bounded')
    if any(ord(char) < 32 for char in value):
        raise ValueError('subscription_id must not contain control characters')
    return value


def _raise_prior_shutdown_error(self) -> None:
    error = self._shutdown_error
    if error is not None:
        raise EventBusShutdownError('Event bus shutdown completed with an executor error') from error


def set_ui_dispatcher(self, dispatcher: Callable[[Callable[[], None]], None]) -> None:
    if not callable(dispatcher):
        raise TypeError('dispatcher must be callable')
    with self._lock:
        if self._shutting_down:
            raise EventBusShutdownError('Cannot set a dispatcher while the bus is shutting down')
        self._ui_dispatcher = dispatcher
    logger.debug('UI dispatcher registered')


def set_rate_limit(
    self,
    event_type: EventKey,
    min_interval_seconds: float = 0.1,
    max_per_interval: int = 10,
) -> None:
    validated_event_type = _validate_event_key(event_type)
    window = RateLimitWindow(min_interval_seconds, max_per_interval)
    with self._lock:
        if self._shutting_down:
            raise EventBusShutdownError('Cannot configure rate limits during shutdown')
        self._rate_limits[validated_event_type] = window
        _mark_observation_changed(self)


def clear_rate_limit(self, event_type: EventKey) -> None:
    validated_event_type = _validate_event_key(event_type)
    with self._lock:
        removed = self._rate_limits.pop(validated_event_type, None)
        if removed is not None:
            _mark_observation_changed(self)


def start(self) -> None:
    with self._lock:
        if self._shutting_down:
            raise EventBusShutdownError('A shutdown AudioEventBus cannot be restarted')
        self._stats['start_time'] = datetime.now()
        _mark_observation_changed(self)
    logger.debug('Event bus started')


def _collect_shutdown_state(
    self,
) -> tuple[
    object | None,
    set[EventWaitState],
    dict[EventKey, list[EventSubscription]],
    dict[EventKey, RateLimitWindow],
    deque[EventRecord],
]:
    with self._publish_condition:
        while self._active_publishes > 0:
            self._publish_condition.wait()
        executor = self._executor
        self._executor = None
        waiters = self._event_waiters
        self._event_waiters = set()
        subscribers = self._subscribers
        self._subscribers = {}
        rate_limits = self._rate_limits
        self._rate_limits = {}
        history = self._event_history
        self._event_history = deque(maxlen=self._max_history_size)
        self._history_version += 1
        self._ui_dispatcher = None
        _mark_observation_changed(self)
    return executor, waiters, subscribers, rate_limits, history


def _remember_shutdown_error(
    current: Exception | None,
    boundary: str,
    error: Exception,
) -> Exception:
    logger.error('Event bus shutdown %s failed: %s', boundary, error, exc_info=True)
    return current if current is not None else error


def _finalize_shutdown_state(
    self,
    waiters: set[EventWaitState],
    subscribers: dict[EventKey, list[EventSubscription]],
    rate_limits: dict[EventKey, RateLimitWindow],
) -> Exception | None:
    error: Exception | None = None
    for waiter in waiters:
        try:
            waiter.fail(EventBusShutdownError('Event bus shutdown cancelled the pending wait'))
        except Exception as cleanup_error:
            error = _remember_shutdown_error(error, 'waiter cancellation', cleanup_error)
    for subscriptions in subscribers.values():
        for subscription in subscriptions:
            try:
                subscription.deactivate()
            except Exception as cleanup_error:
                error = _remember_shutdown_error(error, 'subscription cleanup', cleanup_error)
    for rate_limit in rate_limits.values():
        try:
            rate_limit.reset()
        except Exception as cleanup_error:
            error = _remember_shutdown_error(error, 'rate-limit cleanup', cleanup_error)
    return error


def _shutdown_executor(self, executor: object | None) -> Exception | None:
    if executor is None:
        return None
    wait_for_workers = not bool(getattr(self._callback_context, 'in_async_worker', False))
    try:
        executor.shutdown(wait=wait_for_workers, cancel_futures=True)
        return None
    except Exception as error:
        logger.error('Event bus executor shutdown failed: %s', error, exc_info=True)
        return error


def shutdown(self) -> None:
    """Terminally stop the bus without waiting reentrantly from callbacks.

    Edge cases:
        1. Active synchronous or cross-thread dispatcher callbacks cannot own shutdown.
        2. Callback threads never wait on a shutdown that may already be joining them.
        3. Cleanup failures are terminal, replayable, and always release concurrent callers.
    """
    publish_depth = getattr(self._publish_context, 'depth', 0)
    callback_depth = getattr(self._callback_context, 'depth', 0)
    in_async_worker = bool(getattr(self._callback_context, 'in_async_worker', False))
    if publish_depth > 0:
        raise EventBusShutdownError('Cannot shut down the event bus from an active publish callback')
    with self._lock:
        if callback_depth > 0 and not in_async_worker and self._active_publishes > 0:
            raise EventBusShutdownError('Cannot shut down while a dispatcher callback is publishing')
        if self._shutting_down:
            completion = self._shutdown_complete
            owns_shutdown = False
        else:
            self._shutting_down = True
            _mark_observation_changed(self)
            completion = self._shutdown_complete
            owns_shutdown = True
    if not owns_shutdown:
        if callback_depth > 0:
            return
        completion.wait()
        _raise_prior_shutdown_error(self)
        return

    terminal_error: Exception | None = None
    retained_history: deque[EventRecord] | None = None
    try:
        executor, waiters, subscribers, rate_limits, retained_history = _collect_shutdown_state(self)
        terminal_error = _finalize_shutdown_state(self, waiters, subscribers, rate_limits)
        executor_error = _shutdown_executor(self, executor)
        if terminal_error is None:
            terminal_error = executor_error
    except Exception as error:
        terminal_error = _remember_shutdown_error(terminal_error, 'terminalization', error)
    finally:
        with self._lock:
            self._shutdown_error = terminal_error
        completion.set()
        del retained_history
    logger.debug('AudioEventBus shutdown complete')
    _raise_prior_shutdown_error(self)


def close(self) -> None:
    self.shutdown()


def subscribe(
    self,
    event_type: EventKey,
    callback: Callable[[object], None],
    priority: int = 0,
    filter_condition: Callable[[object], bool] | None = None,
    subscription_id: str | None = None,
    one_time: bool = False,
) -> EventSubscription:
    validated_event_type = _validate_event_key(event_type)
    if not callable(callback):
        raise TypeError('callback must be callable')
    if filter_condition is not None and not callable(filter_condition):
        raise TypeError('filter_condition must be callable or None')
    validated_priority = _validate_priority(priority)
    validated_id = _validate_subscription_id(subscription_id)
    if not isinstance(one_time, bool):
        raise TypeError('one_time must be a boolean')

    with self._lock:
        if self._shutting_down:
            raise EventBusShutdownError('Cannot subscribe while the EventBus is shutting down')
        subscriptions = self._subscribers.setdefault(validated_event_type, [])
        if validated_id is not None and any(
            item.subscription_id == validated_id for item in subscriptions
        ):
            raise ValueError('subscription_id must be unique for the event type')
        subscription = EventSubscription(
            callback=callback,
            priority=validated_priority,
            filter_condition=filter_condition,
            subscription_id=validated_id,
            one_time=one_time,
        )
        subscriptions.append(subscription)
        subscriptions.sort(key=lambda item: item.priority, reverse=True)
        self._stats['subscriptions_created'] += 1
        _mark_observation_changed(self)
    logger.debug('Subscribed to %s with ID %s', validated_event_type, subscription.subscription_id)
    return subscription


def unsubscribe(
    self,
    event_type: EventKey,
    subscription: EventSubscription | None = None,
    callback: Callable[[object], None] | None = None,
    subscription_id: str | None = None,
) -> bool:
    validated_event_type = _validate_event_key(event_type)
    with self._lock:
        subscriptions = self._subscribers.get(validated_event_type)
        if not subscriptions:
            return False
        if subscription is not None:
            if subscription not in subscriptions:
                return False
            subscriptions.remove(subscription)
            subscription.deactivate()
            self._stats['subscriptions_removed'] += 1
            _mark_observation_changed(self)
            return True
        if subscription_id is not None:
            matches = [item for item in subscriptions if item.matches_id(subscription_id)]
        elif callback is not None:
            matches = [item for item in subscriptions if item.matches(callback)]
        else:
            matches = list(subscriptions)
        if not matches:
            return False
        for item in matches:
            item.deactivate()
            subscriptions.remove(item)
        self._stats['subscriptions_removed'] += len(matches)
        _mark_observation_changed(self)
        return True


def unsubscribe_all(self, event_type: EventKey | None = None) -> int:
    with self._lock:
        if event_type is not None:
            validated_event_type = _validate_event_key(event_type)
            targets = {validated_event_type: self._subscribers.get(validated_event_type, [])}
        else:
            targets = dict(self._subscribers)
        total = 0
        for event_key, subscriptions in targets.items():
            for subscription in subscriptions:
                subscription.deactivate()
            total += len(subscriptions)
            self._subscribers[event_key] = []
        self._stats['subscriptions_removed'] += total
        if total > 0:
            _mark_observation_changed(self)
        return total


def has_subscribers(self, event_type: EventKey) -> bool:
    validated_event_type = _validate_event_key(event_type)
    with self._lock:
        return any(item.is_active for item in self._subscribers.get(validated_event_type, ()))


def get_subscriber_count(self, event_type: EventKey) -> int:
    validated_event_type = _validate_event_key(event_type)
    with self._lock:
        return sum(
            1 for item in self._subscribers.get(validated_event_type, ()) if item.is_active
        )


def get_subscriptions(self, event_type: EventKey) -> list[EventSubscription]:
    validated_event_type = _validate_event_key(event_type)
    with self._lock:
        return [
            item for item in self._subscribers.get(validated_event_type, ()) if item.is_active
        ]


def is_alive(self) -> bool:
    with self._lock:
        return not self._shutting_down


_AUDIO_EVENT_BUS_CORE_METHODS: tuple[tuple[str, Callable[..., object]], ...] = (
    ('set_ui_dispatcher', set_ui_dispatcher),
    ('set_rate_limit', set_rate_limit),
    ('clear_rate_limit', clear_rate_limit),
    ('start', start),
    ('shutdown', shutdown),
    ('close', close),
    ('subscribe', subscribe),
    ('unsubscribe', unsubscribe),
    ('unsubscribe_all', unsubscribe_all),
    ('has_subscribers', has_subscribers),
    ('get_subscriber_count', get_subscriber_count),
    ('get_subscriptions', get_subscriptions),
    ('is_alive', is_alive),
)


def install_audio_event_bus_core_behavior(event_bus_cls: type[object]) -> None:
    """Install audio event bus core behavior on the central coordinator."""
    if getattr(event_bus_cls, '_audio_event_bus_core_behavior_attached', False):
        return
    seen_names: set[str] = set()
    for attribute_name, method in _AUDIO_EVENT_BUS_CORE_METHODS:
        if not attribute_name:
            raise TypeError('AudioEventBus core binding name must not be empty')
        if attribute_name in seen_names:
            raise TypeError(f'Duplicate AudioEventBus core binding: {attribute_name}')
        if not callable(method):
            raise TypeError(f'AudioEventBus core binding {attribute_name} must be callable')
        setattr(event_bus_cls, attribute_name, method)
        seen_names.add(attribute_name)
    setattr(event_bus_cls, '_audio_event_bus_core_behavior_attached', True)


def attach_audio_event_bus_core_behavior(event_bus_cls: type[object]) -> None:
    """Compatibility shim for historical attach_* imports."""
    install_audio_event_bus_core_behavior(event_bus_cls)
