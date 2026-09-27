from __future__ import annotations

import logging
import time
from concurrent.futures import CancelledError, Future
from datetime import datetime
from typing import Callable

from .audio_event_bus_core import _validate_event_key
from .audio_event_bus_observation import (
    clear_history,
    get_active_subscriptions,
    get_event_history,
    get_stats,
    record_event as _record_event,
    reset_stats,
    wait_for_event,
    _mark_observation_changed,
)
from .audio_event_models import (
    AudioEventType,
    EventMetadata,
    EventSubscription,
    snapshot_event_payload,
)

logger = logging.getLogger(__name__)

EventKey = AudioEventType | str
EVENT_BUS_PUBLISH_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
_ADMISSION_ACCEPTED = 'accepted'
_ADMISSION_RATE_LIMITED = 'rate_limited'
_ADMISSION_SHUTTING_DOWN = 'shutting_down'
_CALLBACK_COMPLETED = 'completed'
_CALLBACK_FAILED = 'failed'
_CALLBACK_SKIPPED = 'skipped'


def _increment_stat(self, name: str, amount: int = 1) -> None:
    with self._lock:
        current = self._stats[name]
        if isinstance(current, bool) or not isinstance(current, int):
            raise RuntimeError(f'Event bus stat {name!r} is not an integer')
        self._stats[name] = current + amount
        _mark_observation_changed(self)


def _record_boundary_error(
    self,
    event_type: EventKey,
    boundary: str,
    error: Exception,
    subscription_id: str | None = None,
) -> None:
    logger.error(
        'Event bus %s failure for %s (subscription=%s): %s',
        boundary,
        event_type,
        subscription_id,
        error,
        exc_info=True,
    )
    _increment_stat(self, 'errors')


def _enter_publish(self, event_type: EventKey) -> str:
    """Reserve rate-limit and lifecycle capacity in O(1) locked work."""
    with self._publish_condition:
        if self._shutting_down:
            return _ADMISSION_SHUTTING_DOWN
        rate_limit = self._rate_limits.get(event_type)
        if rate_limit is not None and not rate_limit.reserve(time.monotonic()):
            current = self._stats['events_rate_limited']
            if isinstance(current, bool) or not isinstance(current, int):
                raise RuntimeError('Event bus rate-limit counter is not an integer')
            self._stats['events_rate_limited'] = current + 1
            _mark_observation_changed(self)
            return _ADMISSION_RATE_LIMITED
        self._active_publishes += 1
        depth = getattr(self._publish_context, 'depth', 0)
        self._publish_context.depth = depth + 1
        return _ADMISSION_ACCEPTED


def _leave_publish(self) -> None:
    with self._publish_condition:
        depth = getattr(self._publish_context, 'depth', 0)
        if depth < 1 or self._active_publishes < 1:
            raise RuntimeError('Event bus publish lifecycle state is inconsistent')
        self._publish_context.depth = depth - 1
        self._active_publishes -= 1
        if self._active_publishes == 0:
            self._publish_condition.notify_all()


def publish(
    self,
    event_type: EventKey,
    data: object = None,
    source: str | None = None,
    priority: int = 0,
    correlation_id: str | None = None,
    require_ui_thread: bool = False,
) -> bool:
    validated_event_type = _validate_event_key(event_type)
    if not isinstance(require_ui_thread, bool):
        raise TypeError('require_ui_thread must be a boolean')
    admission = _enter_publish(self, validated_event_type)
    if admission == _ADMISSION_SHUTTING_DOWN:
        logger.debug('Ignoring event %s: bus is shutting down', event_type)
        return False
    if admission == _ADMISSION_RATE_LIMITED:
        return False
    try:
        return self._publish_internal(
            event_type=validated_event_type,
            data=data,
            source=source,
            priority=priority,
            correlation_id=correlation_id,
            require_ui_thread=require_ui_thread,
        )
    finally:
        _leave_publish(self)


def _prepare_event(
    self,
    event_type: EventKey,
    source: str | None,
    priority: int,
    correlation_id: str | None,
) -> tuple[EventMetadata, tuple[EventSubscription, ...]]:
    with self._lock:
        self._event_counter += 1
        metadata = EventMetadata(
            timestamp=datetime.now(),
            source=source,
            priority=priority,
            correlation_id=correlation_id,
            event_id=f'evt_{self._event_counter}',
        )
        candidates = tuple(
            subscription
            for subscription in self._subscribers.get(event_type, ())
            if subscription.is_active
        )
    return metadata, candidates


def _subscription_accepts(
    self,
    subscription: EventSubscription,
    event_type: EventKey,
    data: object,
) -> bool:
    with self._lock:
        if not subscription.is_active:
            return False
    filter_condition = subscription.filter_condition
    if filter_condition is not None:
        try:
            if not bool(filter_condition(data)):
                return False
        except Exception as error:
            _record_boundary_error(
                self,
                event_type,
                'filter callback',
                error,
                subscription.subscription_id,
            )
            return False
    with self._lock:
        return subscription.try_claim_delivery()


def _select_subscribers(
    self,
    candidates: tuple[EventSubscription, ...],
    event_type: EventKey,
    data: object,
) -> tuple[EventSubscription, ...]:
    return tuple(
        subscription
        for subscription in candidates
        if _subscription_accepts(self, subscription, event_type, data)
    )


def _publish_internal(
    self,
    event_type: EventKey,
    data: object = None,
    source: str | None = None,
    priority: int = 0,
    correlation_id: str | None = None,
    require_ui_thread: bool = False,
) -> bool:
    try:
        history_data = snapshot_event_payload(data)
        metadata, candidates = _prepare_event(
            self,
            event_type,
            source,
            priority,
            correlation_id,
        )
        subscribers = _select_subscribers(self, candidates, event_type, data)
        start_time = time.perf_counter()
        dispatched_count = sum(
            1
            for subscription in subscribers
            if self._execute_callback(
                subscription=subscription,
                event_type=event_type,
                data=data,
                require_ui_thread=require_ui_thread,
            )
        )
        processing_time = (time.perf_counter() - start_time) * 1000.0
        self._record_event(
            event_type,
            history_data,
            metadata,
            dispatched_count,
            processing_time,
        )
        return True
    except EVENT_BUS_PUBLISH_EXCEPTIONS as error:
        logger.error('Error publishing event %s: %s', event_type, error, exc_info=True)
        _increment_stat(self, 'errors')
        return False


def _finalize_one_time_delivery(
    self,
    subscription: EventSubscription,
    event_type: EventKey,
) -> None:
    if subscription.one_time:
        self.unsubscribe(event_type, subscription=subscription)


def _run_subscription_callback(
    self,
    subscription: EventSubscription,
    event_type: EventKey,
    data: object,
) -> str:
    with self._lock:
        if not subscription.is_active:
            return _CALLBACK_SKIPPED
    callback_depth = getattr(self._callback_context, 'depth', 0)
    self._callback_context.depth = callback_depth + 1
    try:
        try:
            subscription.callback(data)
        except Exception as error:
            _record_boundary_error(
                self,
                event_type,
                'subscriber callback',
                error,
                subscription.subscription_id,
            )
            return _CALLBACK_FAILED
    finally:
        self._callback_context.depth = callback_depth
        _finalize_one_time_delivery(self, subscription, event_type)

    with self._lock:
        if not self._shutting_down:
            subscription.increment_call_count()
            current = self._stats['events_processed']
            if isinstance(current, bool) or not isinstance(current, int):
                raise RuntimeError('Event bus processed counter is not an integer')
            self._stats['events_processed'] = current + 1
            _mark_observation_changed(self)
    return _CALLBACK_COMPLETED


def _run_async_subscription_callback(
    self,
    subscription: EventSubscription,
    event_type: EventKey,
    data: object,
) -> str:
    self._callback_context.in_async_worker = True
    try:
        return _run_subscription_callback(self, subscription, event_type, data)
    finally:
        self._callback_context.in_async_worker = False


def _release_failed_dispatch_claim(self, subscription: EventSubscription) -> None:
    with self._lock:
        subscription.release_delivery_claim()


def _execute_callback(
    self,
    subscription: EventSubscription,
    event_type: EventKey,
    data: object,
    require_ui_thread: bool,
) -> bool:
    with self._lock:
        dispatcher = self._ui_dispatcher if require_ui_thread else None
        executor = self._executor if self._enable_async and dispatcher is None else None

    if dispatcher is not None:
        try:
            dispatcher(lambda: _run_subscription_callback(self, subscription, event_type, data))
            return True
        except Exception as error:
            _release_failed_dispatch_claim(self, subscription)
            _record_boundary_error(
                self,
                event_type,
                'UI dispatcher',
                error,
                subscription.subscription_id,
            )
            return False

    if executor is not None:
        try:
            future = executor.submit(
                _run_async_subscription_callback,
                self,
                subscription,
                event_type,
                data,
            )
            future.add_done_callback(self._handle_async_result)
            return True
        except Exception as error:
            _release_failed_dispatch_claim(self, subscription)
            _record_boundary_error(
                self,
                event_type,
                'async dispatcher',
                error,
                subscription.subscription_id,
            )
            return False

    return _run_subscription_callback(self, subscription, event_type, data) != _CALLBACK_SKIPPED


def _handle_async_result(self, future: Future[str]) -> None:
    try:
        future.result()
    except CancelledError as error:
        with self._lock:
            shutting_down = self._shutting_down
        if not shutting_down:
            _record_boundary_error(self, 'async_worker', 'async cancellation', error)
    except Exception as error:
        _record_boundary_error(self, 'async_worker', 'async completion', error)


_AUDIO_EVENT_BUS_PUBLISH_METHODS: tuple[tuple[str, Callable[..., object]], ...] = (
    ('publish', publish),
    ('_publish_internal', _publish_internal),
    ('_execute_callback', _execute_callback),
    ('_handle_async_result', _handle_async_result),
    ('_enter_publish', _enter_publish),
    ('_leave_publish', _leave_publish),
    ('_record_event', _record_event),
    ('get_event_history', get_event_history),
    ('clear_history', clear_history),
    ('get_stats', get_stats),
    ('reset_stats', reset_stats),
    ('wait_for_event', wait_for_event),
    ('get_active_subscriptions', get_active_subscriptions),
)


def install_audio_event_bus_publish_behavior(event_bus_cls: type[object]) -> None:
    """Install publish and observation behavior on the central coordinator.

    Edge cases:
        1. Empty binding names would mutate unintended bus attributes.
        2. Non-callable split-module bindings would break runtime wiring.
        3. Duplicate binding names would silently shadow an earlier method.
    """
    if getattr(event_bus_cls, '_audio_event_bus_publish_behavior_attached', False):
        return
    seen_names: set[str] = set()
    for attribute_name, method in _AUDIO_EVENT_BUS_PUBLISH_METHODS:
        if not attribute_name:
            raise TypeError('AudioEventBus publish binding name must not be empty')
        if attribute_name in seen_names:
            raise TypeError(f'Duplicate AudioEventBus publish binding: {attribute_name}')
        if not callable(method):
            raise TypeError(f'AudioEventBus publish binding {attribute_name} must be callable')
        setattr(event_bus_cls, attribute_name, method)
        seen_names.add(attribute_name)
    setattr(event_bus_cls, '_audio_event_bus_publish_behavior_attached', True)


def attach_audio_event_bus_publish_behavior(event_bus_cls: type[object]) -> None:
    """Compatibility shim for historical attach_* imports."""
    install_audio_event_bus_publish_behavior(event_bus_cls)
