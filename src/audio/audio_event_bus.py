from __future__ import annotations

import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import RLock
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from .audio_event_bus_core import attach_audio_event_bus_core_behavior as _attach_audio_event_bus_core_behavior
from .audio_event_bus_publish import attach_audio_event_bus_publish_behavior as _attach_audio_event_bus_publish_behavior
from .audio_event_models import AudioEventType, EventRecord, EventSubscription

logger = logging.getLogger(__name__)

_AUDIO_EVENT_BUS_ATTACHERS: tuple[Callable[[type["AudioEventBus"]], None], ...] = (
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
        self._subscribers: Dict[AudioEventType, List[EventSubscription]] = {}
        self._ui_dispatcher: Optional[Callable[[Callable[[], None]], None]] = None
        self._lock = RLock()
        self._event_history: Deque[EventRecord] = deque(maxlen=max_history_size)
        self._max_history_size = max_history_size
        self._shutting_down = False

        self._stats = {
            'events_published': 0,
            'events_processed': 0,
            'subscriptions_created': 0,
            'subscriptions_removed': 0,
            'errors': 0,
            'start_time': datetime.now(),
        }

        self._rate_limits: Dict[AudioEventType, Tuple[float, int]] = {}
        self._last_event_times: Dict[AudioEventType, List[datetime]] = {}

        self._enable_async = enable_async_processing
        self._executor: Optional[ThreadPoolExecutor] = None
        if self._enable_async:
            self._executor = ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix='EventBusWorker',
            )

        self._event_counter = 0

        logger.debug(
            "AudioEventBus initialized with max_history=%s, async=%s",
            max_history_size,
            enable_async_processing,
        )



def attach_audio_event_bus_behavior(event_bus_cls: type["AudioEventBus"]) -> None:
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
