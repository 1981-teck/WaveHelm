from __future__ import annotations

from typing import Optional

from .audio_event_bus import AudioEventBus
from .audio_event_models import AudioEventType, EventMetadata, EventRecord, EventSubscription

_event_bus_instance: Optional[AudioEventBus] = None


def get_global_event_bus() -> AudioEventBus:
    """Get or create the global event bus instance."""
    global _event_bus_instance
    if _event_bus_instance is None:
        _event_bus_instance = AudioEventBus()
        _event_bus_instance.start()
    return _event_bus_instance


def shutdown_global_event_bus() -> None:
    """Shutdown the global event bus."""
    global _event_bus_instance
    if _event_bus_instance is not None:
        _event_bus_instance.shutdown()
        _event_bus_instance = None
