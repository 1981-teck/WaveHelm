from __future__ import annotations

import threading
from concurrent.futures import Future
from datetime import datetime

from src.audio.audio_event_models import AudioEventType, EventSubscription
from src.audio import audio_event_bus_publish as publish_mod


class DummyBus:
    def __init__(self):
        self._lock = threading.RLock()
        self._shutting_down = False
        self._subscribers = {}
        self._event_history = []
        self._stats = {
            'events_published': 0,
            'events_processed': 0,
            'subscriptions_created': 0,
            'subscriptions_removed': 0,
            'errors': 0,
            'start_time': datetime.now(),
        }
        self._event_counter = 0
        self._rate_limits = {}
        self._last_event_times = {}
        self._ui_dispatcher = None
        self._enable_async = False
        self._executor = None

    def subscribe(self, event_type, callback, one_time=False):
        sub = EventSubscription(callback=callback)
        self._subscribers.setdefault(event_type, []).append(sub)
        return sub.subscription_id

    def unsubscribe(self, event_type, subscription=None):
        subs = self._subscribers.get(event_type, [])
        if subscription is None:
            return
        for sub in subs:
            if sub.subscription_id == subscription:
                sub.deactivate()


publish_mod.attach_audio_event_bus_publish_behavior(DummyBus)


def test_publish_records_event_without_subscribers():
    bus = DummyBus()

    assert bus.publish(AudioEventType.PLAYBACK_STARTED, {'path': 'a.mp3'}) is True
    assert bus._stats['events_published'] == 1
    assert len(bus._event_history) == 1
    assert bus._event_history[0].subscribers_notified == 0


def test_execute_callback_returns_false_for_sync_and_ui_failures():
    bus = DummyBus()
    failing = EventSubscription(callback=lambda data: (_ for _ in ()).throw(RuntimeError('boom')), subscription_id='sub-sync')
    assert bus._execute_callback(failing, AudioEventType.PLAYBACK_STARTED, {}, False) is False

    ui_bus = DummyBus()
    ui_bus._ui_dispatcher = lambda callback: (_ for _ in ()).throw(RuntimeError('ui fail'))
    ok_sub = EventSubscription(callback=lambda data: None, subscription_id='sub-ui')
    assert ui_bus._execute_callback(ok_sub, AudioEventType.PLAYBACK_STARTED, {}, True) is False


def test_handle_async_result_increments_error_count():
    bus = DummyBus()
    future = Future()
    future.set_exception(RuntimeError('async fail'))

    bus._handle_async_result(future)
    assert bus._stats['errors'] == 1


def test_wait_for_event_returns_matching_payload_and_unsubscribes():
    bus = DummyBus()

    def emit_later(callback):
        callback({'value': 2})

    result = bus.wait_for_event(
        AudioEventType.PLAYBACK_PROGRESS,
        timeout=0.1,
        condition=lambda data: data.get('value') == 2,
    )
    assert result is None

    subs = bus._subscribers[AudioEventType.PLAYBACK_PROGRESS]
    subs[0].callback({'value': 2})
    matched = bus.wait_for_event(
        AudioEventType.PLAYBACK_STOPPED,
        timeout=0.0,
        condition=lambda data: data.get('done') is True,
    )
    assert matched is None
