from __future__ import annotations

import threading
from datetime import datetime

import pytest

from src.audio import audio_event_bus_core as core_mod
from src.audio.audio_event_models import AudioEventType, EventSubscription


class DummyExecutor:
    def __init__(self):
        self.calls = []

    def shutdown(self, wait=False):
        self.calls.append(wait)


class DummyBus:
    def __init__(self):
        self._lock = threading.RLock()
        self._shutting_down = False
        self._subscribers = {
            AudioEventType.PLAYBACK_STARTED: [EventSubscription(callback=lambda data: None)],
        }
        self._event_history = ['event']
        self._last_event_times = {AudioEventType.PLAYBACK_STARTED: [datetime.now()]}
        self._executor = DummyExecutor()
        self._stats = {
            'subscriptions_removed': 0,
            'subscriptions_created': 0,
            'start_time': None,
        }
        self.published = []

    def _publish_internal(self, event_type, payload, source=None):
        self.published.append((event_type, payload, source))
        raise RuntimeError('publish fail')


core_mod.attach_audio_event_bus_core_behavior(DummyBus)


def test_shutdown_clears_state_and_tolerates_internal_publish_failure():
    bus = DummyBus()

    bus.shutdown()

    assert bus._shutting_down is True
    assert bus._executor is None
    assert bus._subscribers == {}
    assert bus._event_history == []
    assert bus._last_event_times == {}
    assert len(bus.published) == 1


def test_one_time_subscription_unsubscribes_even_if_callback_raises():
    bus = DummyBus()
    bus._subscribers = {}

    subscription = bus.subscribe(
        AudioEventType.PLAYBACK_STARTED,
        lambda data: (_ for _ in ()).throw(RuntimeError('boom')),
        one_time=True,
    )

    with pytest.raises(RuntimeError, match='boom'):
        subscription.callback({'path': 'x'})

    assert bus.get_subscriber_count(AudioEventType.PLAYBACK_STARTED) == 0
