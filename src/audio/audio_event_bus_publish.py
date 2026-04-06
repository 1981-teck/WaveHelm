from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .audio_event_models import AudioEventType, EventMetadata, EventRecord, EventSubscription

logger = logging.getLogger(__name__)

EVENT_BUS_PUBLISH_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
EVENT_BUS_CALLBACK_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
EVENT_BUS_ASYNC_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)


def publish(
    self,
    event_type: AudioEventType,
    data: Any = None,
    source: Optional[str] = None,
    priority: int = 0,
    correlation_id: Optional[str] = None,
    require_ui_thread: bool = False,
) -> bool:
    with self._lock:
        if self._shutting_down:
            logger.debug("Ignoring event %s: bus is shutting down", event_type)
            return False

    if not self._check_rate_limit(event_type):
        logger.debug("Event %s rate limited", event_type)
        return False

    return self._publish_internal(
        event_type=event_type,
        data=data,
        source=source,
        priority=priority,
        correlation_id=correlation_id,
        require_ui_thread=require_ui_thread,
    )


def _publish_internal(
    self,
    event_type: AudioEventType,
    data: Any = None,
    source: Optional[str] = None,
    priority: int = 0,
    correlation_id: Optional[str] = None,
    require_ui_thread: bool = False,
) -> bool:
    with self._lock:
        self._event_counter += 1
        event_id = f"evt_{self._event_counter}"

        metadata = EventMetadata(
            timestamp=datetime.now(),
            source=source,
            priority=priority,
            correlation_id=correlation_id,
            event_id=event_id,
        )

        if event_type not in self._subscribers:
            subscribers = []
        else:
            subscribers = [
                s for s in self._subscribers[event_type]
                if s.is_active and s.should_receive(data)
            ]

    if not subscribers:
        self._record_event(event_type, data, metadata, 0, 0.0)
        self._stats['events_published'] += 1
        return True

    self._update_rate_limit(event_type)
    start_time = time.time()
    notified_count = 0

    try:
        for subscription in subscribers:
            if self._execute_callback(
                subscription=subscription,
                event_type=event_type,
                data=data,
                require_ui_thread=require_ui_thread,
            ):
                notified_count += 1
                subscription.increment_call_count()

        processing_time = (time.time() - start_time) * 1000
        self._record_event(event_type, data, metadata, notified_count, processing_time)
        self._stats['events_published'] += 1
        self._stats['events_processed'] += notified_count
        return True

    except EVENT_BUS_PUBLISH_EXCEPTIONS as e:
        logger.error("Error publishing event %s: %s", event_type, e, exc_info=True)
        self._stats['errors'] += 1
        return False


def _execute_callback(
    self,
    subscription: EventSubscription,
    event_type: AudioEventType,
    data: Any,
    require_ui_thread: bool,
) -> bool:
    def _run() -> None:
        try:
            subscription.callback(data)
        except EVENT_BUS_CALLBACK_EXCEPTIONS as e:
            logger.error(
                "Error in callback for %s (subscription %s): %s",
                event_type,
                subscription.subscription_id,
                e,
                exc_info=True,
            )
            raise

    use_ui_thread = require_ui_thread and self._ui_dispatcher is not None
    use_async = self._enable_async and not use_ui_thread

    if use_ui_thread:
        try:
            self._ui_dispatcher(_run)
            return True
        except EVENT_BUS_CALLBACK_EXCEPTIONS as e:
            logger.error("UI dispatcher failed for %s: %s", event_type, e)
            return False

    if use_async and self._executor:
        future = self._executor.submit(_run)
        future.add_done_callback(self._handle_async_result)
        return True

    try:
        _run()
        return True
    except EVENT_BUS_CALLBACK_EXCEPTIONS:
        return False


def _handle_async_result(self, future: Future) -> None:
    try:
        future.result()
    except EVENT_BUS_ASYNC_EXCEPTIONS as e:
        logger.error("Async callback execution failed: %s", e)
        self._stats['errors'] += 1


def _check_rate_limit(self, event_type: AudioEventType) -> bool:
    with self._lock:
        if event_type not in self._rate_limits:
            return True

        min_interval, max_count = self._rate_limits[event_type]
        now = datetime.now()
        timestamps = self._last_event_times[event_type]
        cutoff = now.timestamp() - min_interval
        timestamps = [ts for ts in timestamps if ts.timestamp() > cutoff]

        if len(timestamps) >= max_count:
            return False
        return True


def _update_rate_limit(self, event_type: AudioEventType) -> None:
    with self._lock:
        if event_type in self._rate_limits:
            timestamps = self._last_event_times.get(event_type, [])
            timestamps.append(datetime.now())
            min_interval, _ = self._rate_limits[event_type]
            cutoff = datetime.now().timestamp() - min_interval
            timestamps = [ts for ts in timestamps if ts.timestamp() > cutoff]
            self._last_event_times[event_type] = timestamps


def _record_event(
    self,
    event_type: AudioEventType,
    data: Any,
    metadata: EventMetadata,
    subscribers_notified: int,
    processing_time_ms: float,
) -> None:
    record = EventRecord(
        event_type=event_type,
        data=data,
        metadata=metadata,
        subscribers_notified=subscribers_notified,
        processing_time_ms=processing_time_ms,
    )
    with self._lock:
        self._event_history.append(record)


def get_event_history(
    self,
    event_type: Optional[AudioEventType] = None,
    limit: int = 100,
    since: Optional[datetime] = None,
) -> List[EventRecord]:
    with self._lock:
        history = list(self._event_history)
        if event_type is not None:
            history = [e for e in history if e.event_type == event_type]
        if since is not None:
            history = [e for e in history if e.metadata.timestamp > since]
        return history[-limit:]


def clear_history(self) -> None:
    with self._lock:
        self._event_history.clear()


def get_stats(self) -> Dict[str, Any]:
    with self._lock:
        uptime = (datetime.now() - self._stats['start_time']).total_seconds()

        subscriptions_by_type = {}
        for event_type, subs in self._subscribers.items():
            active_count = sum(1 for s in subs if s.is_active)
            if active_count > 0:
                subscriptions_by_type[event_type.value] = active_count

        events_by_type = {}
        for record in self._event_history:
            et = record.event_type.value
            events_by_type[et] = events_by_type.get(et, 0) + 1

        return {
            'uptime_seconds': uptime,
            'events_published': self._stats['events_published'],
            'events_processed': self._stats['events_processed'],
            'subscriptions_active': self._stats['subscriptions_created'] - self._stats['subscriptions_removed'],
            'subscriptions_created': self._stats['subscriptions_created'],
            'subscriptions_removed': self._stats['subscriptions_removed'],
            'errors': self._stats['errors'],
            'history_size': len(self._event_history),
            'subscriptions_by_type': subscriptions_by_type,
            'events_by_type': events_by_type,
            'rate_limits': {k.value: v for k, v in self._rate_limits.items()},
            'shutting_down': self._shutting_down,
        }


def reset_stats(self) -> None:
    with self._lock:
        self._stats = {
            'events_published': 0,
            'events_processed': 0,
            'subscriptions_created': 0,
            'subscriptions_removed': 0,
            'errors': 0,
            'start_time': datetime.now(),
        }
        self._event_counter = 0


def wait_for_event(
    self,
    event_type: AudioEventType,
    timeout: float = 10.0,
    condition: Optional[Callable[[Any], bool]] = None,
) -> Optional[Any]:
    event_received = threading.Event()
    received_data = []

    def callback(data: Any) -> None:
        if condition is None or condition(data):
            received_data.append(data)
            event_received.set()

    subscription = self.subscribe(
        event_type=event_type,
        callback=callback,
        one_time=True,
    )

    try:
        if event_received.wait(timeout=timeout):
            return received_data[0] if received_data else None
        return None
    finally:
        self.unsubscribe(event_type, subscription=subscription)


def get_active_subscriptions(self) -> Dict[AudioEventType, List[str]]:
    with self._lock:
        result = {}
        for event_type, subscriptions in self._subscribers.items():
            active_ids = [s.subscription_id for s in subscriptions if s.is_active]
            if active_ids:
                result[event_type] = active_ids
        return result


_AUDIO_EVENT_BUS_PUBLISH_METHODS: tuple[tuple[str, Callable[..., Any]], ...] = (
    ("publish", publish),
    ("_publish_internal", _publish_internal),
    ("_execute_callback", _execute_callback),
    ("_handle_async_result", _handle_async_result),
    ("_check_rate_limit", _check_rate_limit),
    ("_update_rate_limit", _update_rate_limit),
    ("_record_event", _record_event),
    ("get_event_history", get_event_history),
    ("clear_history", clear_history),
    ("get_stats", get_stats),
    ("reset_stats", reset_stats),
    ("wait_for_event", wait_for_event),
    ("get_active_subscriptions", get_active_subscriptions),
)


def install_audio_event_bus_publish_behavior(event_bus_cls: type[Any]) -> None:
    """Install audio event bus publish behavior on the central coordinator.

    Edge cases:
        1. An empty binding name mutates an unintended bus attribute.
        2. A split module exports a non-callable binding and breaks event publication wiring.
        3. Duplicate binding names silently shadow an earlier publish bus method.
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


def attach_audio_event_bus_publish_behavior(event_bus_cls: type[Any]) -> None:
    """Compatibility shim for historical attach_* imports.

    Prefer install_audio_event_bus_publish_behavior() from the central coordinator path.
    """
    install_audio_event_bus_publish_behavior(event_bus_cls)
