from __future__ import annotations

import math
import threading

import pytest

from src.audio import audio_event_bus_core as core_mod
from src.audio.audio_event_bus import AudioEventBus
from src.audio.audio_event_models import AudioEventType, EventSubscription
from src.audio.audio_event_rate_limit import MAX_RATE_LIMIT_EVENTS, RateLimitWindow


class DummyExecutor:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def shutdown(self, wait: bool = False, cancel_futures: bool = False) -> None:
        self.calls.append((wait, cancel_futures))


class DummyBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._publish_condition = threading.Condition(self._lock)
        self._publish_context = threading.local()
        self._callback_context = threading.local()
        self._active_publishes = 0
        self._shutting_down = False
        self._subscribers = {
            AudioEventType.PLAYBACK_STARTED: [
                EventSubscription(callback=lambda data: None)
            ],
        }
        self._event_history = ['event']
        self._history_version = 0
        self._observation_version = 0
        self._max_history_size = 10
        self._event_waiters = set()
        rate_limit = RateLimitWindow(10.0, 2)
        assert rate_limit.reserve(1.0) is True
        self._rate_limits = {AudioEventType.PLAYBACK_STARTED: rate_limit}
        self._ui_dispatcher = None
        self._executor = DummyExecutor()
        self._shutdown_complete = threading.Event()
        self._shutdown_error = None
        self._stats = {
            'subscriptions_removed': 0,
            'subscriptions_created': 0,
            'start_time': None,
        }
        self.published: list[tuple[object, object, object]] = []

    def _publish_internal(self, event_type, payload, source=None):
        self.published.append((event_type, payload, source))
        raise RuntimeError('publish fail')


core_mod.attach_audio_event_bus_core_behavior(DummyBus)


def test_shutdown_clears_state_and_resets_rate_windows() -> None:
    bus = DummyBus()
    rate_window = bus._rate_limits[AudioEventType.PLAYBACK_STARTED]
    executor = bus._executor

    bus.shutdown()

    assert bus._shutting_down is True
    assert bus._executor is None
    assert bus._subscribers == {}
    assert list(bus._event_history) == []
    assert bus._rate_limits == {}
    assert rate_window.used == 0
    assert executor.calls == [(True, True)]
    assert bus.published == []


def test_one_time_subscription_unsubscribes_even_if_callback_raises() -> None:
    bus = AudioEventBus()
    subscription = bus.subscribe(
        AudioEventType.PLAYBACK_STARTED,
        lambda data: (_ for _ in ()).throw(RuntimeError('boom')),
        one_time=True,
    )

    assert bus.publish(AudioEventType.PLAYBACK_STARTED, {'path': 'x'}) is True

    assert bus.get_subscriber_count(AudioEventType.PLAYBACK_STARTED) == 0
    assert subscription.call_count == 0
    assert bus.get_stats()['errors'] == 1


def test_set_rate_limit_builds_a_bounded_preallocated_window() -> None:
    bus = DummyBus()

    bus.set_rate_limit(AudioEventType.PLAYBACK_PROGRESS, 0.5, 3)

    window = bus._rate_limits[AudioEventType.PLAYBACK_PROGRESS]
    assert window.config == (0.5, 3)
    assert window.capacity == 3
    assert window.used == 0


def test_set_rate_limit_rejects_ambiguous_or_unbounded_values() -> None:
    bus = DummyBus()

    for interval in (True, None, 0, -1, math.nan, math.inf):
        with pytest.raises((TypeError, ValueError)):
            bus.set_rate_limit(AudioEventType.PLAYBACK_PROGRESS, interval, 1)

    for count in (True, None, 0, -1, MAX_RATE_LIMIT_EVENTS + 1):
        with pytest.raises((TypeError, ValueError)):
            bus.set_rate_limit(AudioEventType.PLAYBACK_PROGRESS, 1.0, count)


def test_string_event_subscription_can_be_removed_consistently() -> None:
    bus = DummyBus()
    bus._subscribers = {}
    callback = lambda data: None

    subscription = bus.subscribe('VIDEO_READY', callback)

    assert bus.has_subscribers('VIDEO_READY') is True
    assert bus.unsubscribe('VIDEO_READY', subscription=subscription) is True
    assert bus.has_subscribers('VIDEO_READY') is False



def test_set_ui_dispatcher_rejects_non_callable_values() -> None:
    bus = DummyBus()

    with pytest.raises(TypeError, match='dispatcher must be callable'):
        bus.set_ui_dispatcher(None)

    dispatcher = lambda callback: callback()
    bus.set_ui_dispatcher(dispatcher)
    assert bus._ui_dispatcher is dispatcher
