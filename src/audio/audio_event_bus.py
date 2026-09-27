from __future__ import annotations

import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading
from typing import Callable

from .audio_event_bus_core import attach_audio_event_bus_core_behavior as _attach_audio_event_bus_core_behavior
from .audio_event_bus_publish import attach_audio_event_bus_publish_behavior as _attach_audio_event_bus_publish_behavior
from .audio_event_bus_observation import EventWaitState, validate_history_size
from .audio_event_models import AudioEventType, EventRecord, EventSubscription
from .audio_event_rate_limit import RateLimitWindow

logger = logging.getLogger(__name__)

EventKey = AudioEventType | str
MAX_EVENT_BUS_WORKERS = 64
_AUDIO_EVENT_BUS_ATTACHERS: tuple[Callable[[type['AudioEventBus']], None], ...] = (
    _attach_audio_event_bus_core_behavior,
    _attach_audio_event_bus_publish_behavior,
)


class AudioEventBus:
    """Thread-safe EventBus for managing audio/system events."""

    def __init__(
        self,
        max_history_size: int = 1000,
        enable_async_processing: bool = False,
        max_workers: int = 4,
    ) -> None:
        history_size = validate_history_size(max_history_size)
        async_enabled = self._validate_async_enabled(enable_async_processing)
        worker_count = self._validate_worker_count(max_workers)
        self._subscribers: dict[EventKey, list[EventSubscription]] = {}
        self._ui_dispatcher: Callable[[Callable[[], None]], None] | None = None
        self._lock = threading.RLock()
        self._publish_condition = threading.Condition(self._lock)
        self._publish_context = threading.local()
        self._callback_context = threading.local()
        self._event_history: deque[EventRecord] = deque(maxlen=history_size)
        self._history_version = 0
        self._observation_version = 0
        self._event_waiters: set[EventWaitState] = set()
        self._max_history_size = history_size
        self._shutting_down = False
        self._shutdown_complete = threading.Event()
        self._shutdown_error: Exception | None = None
        self._active_publishes = 0

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
        self._events_by_type: dict[str, int] = {}
        self._rate_limits: dict[EventKey, RateLimitWindow] = {}

        self._enable_async = async_enabled
        self._max_workers = worker_count
        self._executor_thread_prefix = f'EventBusWorker-{id(self):x}'
        self._executor: ThreadPoolExecutor | None = None
        if self._enable_async:
            self._executor = ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix=self._executor_thread_prefix,
            )

        self._event_counter = 0

        logger.debug(
            'AudioEventBus initialized with max_history=%s, async=%s',
            max_history_size,
            enable_async_processing,
        )

    @staticmethod
    def _validate_async_enabled(value: object) -> bool:
        if not isinstance(value, bool):
            raise TypeError('enable_async_processing must be a boolean')
        return value

    @staticmethod
    def _validate_worker_count(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError('max_workers must be an integer')
        if value < 1 or value > MAX_EVENT_BUS_WORKERS:
            raise ValueError(f'max_workers must be between 1 and {MAX_EVENT_BUS_WORKERS}')
        return value


def attach_audio_event_bus_behavior(event_bus_cls: type[AudioEventBus]) -> None:
    """Attach audio event bus behavior from one central coordinator.

    Edge cases handled:
        - repeated imports or reloads can silently rebind bus methods;
        - a partial split import can leave the event bus only partially patched;
        - reordered installers can break core or publish behavior deterministically.
    """
    if not isinstance(event_bus_cls, type):
        raise TypeError('event_bus_cls must be a class')
    if getattr(event_bus_cls, '_audio_event_bus_behavior_attached', False):
        return

    for installer in _AUDIO_EVENT_BUS_ATTACHERS:
        installer(event_bus_cls)

    setattr(event_bus_cls, '_audio_event_bus_behavior_attached', True)


attach_audio_event_bus_behavior(AudioEventBus)
