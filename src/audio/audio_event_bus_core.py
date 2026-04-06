from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, List, Optional

from .audio_event_models import AudioEventType, EventSubscription

logger = logging.getLogger(__name__)

AUDIO_EVENT_BUS_SHUTDOWN_EXCEPTIONS = (
    AttributeError,
    RuntimeError,
    TypeError,
    ValueError,
)


def set_ui_dispatcher(self, dispatcher: Callable[[Callable[[], None]], None]) -> None:
    self._ui_dispatcher = dispatcher
    logger.debug("UI dispatcher registered")


def set_rate_limit(
    self,
    event_type: AudioEventType,
    min_interval_seconds: float = 0.1,
    max_per_interval: int = 10,
) -> None:
    with self._lock:
        self._rate_limits[event_type] = (min_interval_seconds, max_per_interval)
        self._last_event_times[event_type] = []


def clear_rate_limit(self, event_type: AudioEventType) -> None:
    with self._lock:
        self._rate_limits.pop(event_type, None)
        self._last_event_times.pop(event_type, None)


def start(self) -> None:
    with self._lock:
        self._shutting_down = False
        self._stats['start_time'] = datetime.now()
        logger.debug("Event bus started")


def shutdown(self) -> None:
    with self._lock:
        if self._shutting_down:
            return

        self._shutting_down = True
        for event_type in list(self._subscribers.keys()):
            for subscription in self._subscribers[event_type]:
                subscription.deactivate()
            self._subscribers[event_type].clear()

        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor = None

        self._subscribers.clear()
        self._event_history.clear()
        self._last_event_times.clear()
        logger.debug("AudioEventBus shutdown complete")

        try:
            self._publish_internal(
                AudioEventType.EVENT_BUS_SHUTDOWN,
                {"timestamp": datetime.now()},
                source="AudioEventBus",
            )
        except AUDIO_EVENT_BUS_SHUTDOWN_EXCEPTIONS:
            logger.debug("Failed publishing shutdown event.", exc_info=True)


def close(self) -> None:
    self.shutdown()


def subscribe(
    self,
    event_type: AudioEventType,
    callback: Callable[[Any], None],
    priority: int = 0,
    filter_condition: Optional[Callable[[Any], bool]] = None,
    subscription_id: Optional[str] = None,
    one_time: bool = False,
) -> EventSubscription:
    with self._lock:
        if self._shutting_down:
            raise RuntimeError("Cannot subscribe: EventBus is shutting down")

        subscription = EventSubscription(
            callback=callback,
            priority=priority,
            filter_condition=filter_condition,
            subscription_id=subscription_id,
        )

        if event_type not in self._subscribers:
            self._subscribers[event_type] = []

        self._subscribers[event_type].append(subscription)
        self._subscribers[event_type].sort(key=lambda s: s.priority, reverse=True)
        self._stats['subscriptions_created'] += 1

        logger.debug(
            "Subscribed to %s with ID %s, priority %s",
            event_type,
            subscription.subscription_id,
            priority,
        )

        if one_time:
            original_callback = subscription.callback

            def one_time_wrapper(data: Any) -> None:
                try:
                    original_callback(data)
                finally:
                    self.unsubscribe(event_type, subscription=subscription)

            subscription.callback = one_time_wrapper

        return subscription


def unsubscribe(
    self,
    event_type: AudioEventType,
    subscription: Optional[EventSubscription] = None,
    callback: Optional[Callable[[Any], None]] = None,
    subscription_id: Optional[str] = None,
) -> bool:
    with self._lock:
        if event_type not in self._subscribers:
            return False

        if subscription is not None:
            try:
                subscription.deactivate()
                self._subscribers[event_type].remove(subscription)
                self._stats['subscriptions_removed'] += 1
                logger.debug(
                    "Unsubscribed subscription %s from %s",
                    subscription.subscription_id,
                    event_type,
                )
                return True
            except ValueError:
                return False

        if subscription_id is not None:
            initial_count = len(self._subscribers[event_type])
            for sub in self._subscribers[event_type]:
                if sub.matches_id(subscription_id):
                    sub.deactivate()
            self._subscribers[event_type] = [
                s for s in self._subscribers[event_type] if not s.matches_id(subscription_id)
            ]
            removed = initial_count - len(self._subscribers[event_type])
            if removed > 0:
                self._stats['subscriptions_removed'] += removed
                logger.debug("Unsubscribed %d subscriptions by ID from %s", removed, event_type)
                return True
            return False

        if callback is not None:
            initial_count = len(self._subscribers[event_type])
            for sub in self._subscribers[event_type]:
                if sub.matches(callback):
                    sub.deactivate()
            self._subscribers[event_type] = [
                s for s in self._subscribers[event_type] if not s.matches(callback)
            ]
            removed = initial_count - len(self._subscribers[event_type])
            if removed > 0:
                self._stats['subscriptions_removed'] += removed
                logger.debug("Unsubscribed %d subscriptions with callback from %s", removed, event_type)
                return True
            return False

        count = len(self._subscribers[event_type])
        for sub in self._subscribers[event_type]:
            sub.deactivate()
        self._subscribers[event_type].clear()
        self._stats['subscriptions_removed'] += count
        logger.debug("Unsubscribed all %d subscriptions from %s", count, event_type)
        return count > 0


def unsubscribe_all(self, event_type: Optional[AudioEventType] = None) -> int:
    with self._lock:
        if event_type is not None:
            if event_type in self._subscribers:
                count = len(self._subscribers[event_type])
                for sub in self._subscribers[event_type]:
                    sub.deactivate()
                self._subscribers[event_type].clear()
                self._stats['subscriptions_removed'] += count
                logger.debug("Unsubscribed all %d subscriptions from %s", count, event_type)
                return count
            return 0

        total = 0
        for et in list(self._subscribers.keys()):
            count = len(self._subscribers[et])
            for sub in self._subscribers[et]:
                sub.deactivate()
            total += count
            self._subscribers[et].clear()
        self._stats['subscriptions_removed'] += total
        logger.debug("Unsubscribed all %d subscriptions from all events", total)
        return total


def has_subscribers(self, event_type: AudioEventType) -> bool:
    with self._lock:
        return event_type in self._subscribers and any(s.is_active for s in self._subscribers[event_type])


def get_subscriber_count(self, event_type: AudioEventType) -> int:
    with self._lock:
        if event_type not in self._subscribers:
            return 0
        return sum(1 for s in self._subscribers[event_type] if s.is_active)


def get_subscriptions(self, event_type: AudioEventType) -> List[EventSubscription]:
    with self._lock:
        if event_type not in self._subscribers:
            return []
        return [s for s in self._subscribers[event_type] if s.is_active]


def is_alive(self) -> bool:
    with self._lock:
        return not self._shutting_down


_AUDIO_EVENT_BUS_CORE_METHODS: tuple[tuple[str, Callable[..., Any]], ...] = (
    ("set_ui_dispatcher", set_ui_dispatcher),
    ("set_rate_limit", set_rate_limit),
    ("clear_rate_limit", clear_rate_limit),
    ("start", start),
    ("shutdown", shutdown),
    ("close", close),
    ("subscribe", subscribe),
    ("unsubscribe", unsubscribe),
    ("unsubscribe_all", unsubscribe_all),
    ("has_subscribers", has_subscribers),
    ("get_subscriber_count", get_subscriber_count),
    ("get_subscriptions", get_subscriptions),
    ("is_alive", is_alive),
)


def install_audio_event_bus_core_behavior(event_bus_cls: type[Any]) -> None:
    """Install audio event bus core behavior on the central coordinator.

    Edge cases:
        1. An empty binding name mutates an unintended bus attribute.
        2. A split module exports a non-callable binding and breaks bus wiring.
        3. Duplicate binding names silently shadow an earlier core bus method.
    """
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


def attach_audio_event_bus_core_behavior(event_bus_cls: type[Any]) -> None:
    """Compatibility shim for historical attach_* imports.

    Prefer install_audio_event_bus_core_behavior() from the central coordinator path.
    """
    install_audio_event_bus_core_behavior(event_bus_cls)
