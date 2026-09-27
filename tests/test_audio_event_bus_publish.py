from __future__ import annotations

import threading
from concurrent.futures import Future
from datetime import datetime

from src.audio import audio_event_bus_publish as publish_mod
from src.audio.audio_event_models import AudioEventType, EventSubscription


class CallbackBoundaryError(Exception):
    pass


class DummyBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._publish_condition = threading.Condition(self._lock)
        self._publish_context = threading.local()
        self._callback_context = threading.local()
        self._active_publishes = 0
        self._shutting_down = False
        self._subscribers: dict[object, list[EventSubscription]] = {}
        self._event_history: list[object] = []
        self._history_version = 0
        self._observation_version = 0
        self._stats: dict[str, object] = {
            'events_published': 0,
            'events_processed': 0,
            'callbacks_dispatched': 0,
            'events_rate_limited': 0,
            'subscriptions_created': 0,
            'subscriptions_removed': 0,
            'errors': 0,
            'start_time': datetime.now(),
        }
        self._event_counter = 0
        self._event_waiters = set()
        self._events_by_type: dict[str, int] = {}
        self._rate_limits = {}
        self._ui_dispatcher = None
        self._enable_async = False
        self._executor = None

    def subscribe(
        self, event_type, callback, priority=0, filter_condition=None, one_time=False
    ):
        subscription = EventSubscription(
            callback=callback, priority=priority, filter_condition=filter_condition, one_time=one_time
        )
        self._subscribers.setdefault(event_type, []).append(subscription)
        return subscription

    def unsubscribe(self, event_type, subscription=None):
        subscriptions = self._subscribers.get(event_type, [])
        if subscription in subscriptions:
            subscription.deactivate()
            subscriptions.remove(subscription)
            return True
        return False


publish_mod.attach_audio_event_bus_publish_behavior(DummyBus)


def test_publish_records_event_without_subscribers() -> None:
    bus = DummyBus()

    assert bus.publish(AudioEventType.PLAYBACK_STARTED, {'path': 'a.mp3'}) is True

    stats = bus.get_stats()
    assert stats['events_published'] == 1
    assert stats['events_processed'] == 0
    assert len(bus._event_history) == 1
    assert bus._event_history[0].subscribers_notified == 0


def test_sync_callback_boundary_isolates_arbitrary_exceptions() -> None:
    for error in (KeyError('key'), OSError('io'), CallbackBoundaryError('custom')):
        bus = DummyBus()
        subscription = EventSubscription(
            callback=lambda data, failure=error: (_ for _ in ()).throw(failure),
            subscription_id='sub-sync',
        )

        assert bus._execute_callback(
            subscription,
            AudioEventType.PLAYBACK_STARTED,
            {},
            False,
        ) is True
        assert bus._stats['errors'] == 1
        assert subscription.call_count == 0


def test_ui_dispatcher_boundary_isolates_arbitrary_exception() -> None:
    bus = DummyBus()
    bus._ui_dispatcher = lambda callback: (_ for _ in ()).throw(KeyError('ui fail'))
    subscription = EventSubscription(callback=lambda data: None, subscription_id='sub-ui')

    assert bus._execute_callback(
        subscription,
        AudioEventType.PLAYBACK_STARTED,
        {},
        True,
    ) is False
    assert bus._stats['errors'] == 1
    assert subscription.call_count == 0


def test_ui_dispatcher_counts_callback_only_after_execution() -> None:
    bus = DummyBus()
    queued_callbacks: list[object] = []
    bus._ui_dispatcher = queued_callbacks.append
    subscription = EventSubscription(callback=lambda data: None, subscription_id='sub-ui')

    assert bus._execute_callback(
        subscription,
        AudioEventType.PLAYBACK_STARTED,
        {},
        True,
    ) is True
    assert subscription.call_count == 0

    queued_callbacks[0]()
    assert subscription.call_count == 1
    assert bus._stats['errors'] == 0



def test_async_dispatcher_boundary_isolates_submit_failure() -> None:
    class FailingExecutor:
        def submit(self, *args, **kwargs):
            raise KeyError('executor rejected submission')

    bus = DummyBus()
    bus._enable_async = True
    bus._executor = FailingExecutor()
    subscription = EventSubscription(callback=lambda data: None, subscription_id='sub-async')

    assert bus._execute_callback(
        subscription,
        AudioEventType.PLAYBACK_STARTED,
        {},
        False,
    ) is False
    assert bus._stats['errors'] == 1
    assert subscription.call_count == 0

def test_handle_async_result_increments_error_count_for_internal_failure() -> None:
    bus = DummyBus()
    future: Future[bool] = Future()
    future.set_exception(CallbackBoundaryError('async fail'))

    bus._handle_async_result(future)

    assert bus._stats['errors'] == 1


def test_filter_boundary_isolates_arbitrary_exception() -> None:
    bus = DummyBus()
    callback_calls: list[object] = []
    subscription = EventSubscription(
        callback=callback_calls.append,
        filter_condition=lambda data: (_ for _ in ()).throw(OSError('filter fail')),
        subscription_id='sub-filter',
    )
    bus._subscribers[AudioEventType.PLAYBACK_STARTED] = [subscription]

    assert bus.publish(AudioEventType.PLAYBACK_STARTED, {'value': 1}) is True

    assert callback_calls == []
    assert bus._stats['errors'] == 1
    assert bus._event_history[0].subscribers_notified == 0


def test_string_event_keys_are_supported_by_stats() -> None:
    bus = DummyBus()

    assert bus.publish('VIDEO_READY', {'ok': True}) is True

    stats = bus.get_stats()
    assert stats['events_by_type'] == {'VIDEO_READY': 1}


def test_active_subscription_stats_do_not_depend_on_reset_counters() -> None:
    bus = DummyBus()
    bus._subscribers[AudioEventType.PLAYBACK_STARTED] = [
        EventSubscription(callback=lambda data: None),
        EventSubscription(callback=lambda data: None),
    ]

    bus.reset_stats()
    bus.unsubscribe(
        AudioEventType.PLAYBACK_STARTED,
        subscription=bus._subscribers[AudioEventType.PLAYBACK_STARTED][0],
    )

    stats = bus.get_stats()
    assert stats['subscriptions_active'] == 1
    assert stats['subscriptions_created'] == 0
    assert stats['subscriptions_removed'] == 0


def test_wait_for_event_timeout_unsubscribes() -> None:
    bus = DummyBus()

    result = bus.wait_for_event(
        AudioEventType.PLAYBACK_STOPPED,
        timeout=0.0,
        condition=lambda data: True,
    )

    assert result is None
    assert bus._subscribers[AudioEventType.PLAYBACK_STOPPED] == []
