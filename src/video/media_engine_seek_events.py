"""Generation-bound native seek notifications, with no clock query in the callback.

An old notify object, an unstarted request or an unordered event pair cannot confirm
current intent. The SDK provides no request token: serialized native seeks and the
provider's ordered notification contract are required. This is not event attestation.
"""
from __future__ import annotations

import logging
from typing import Callable, Protocol, TypeVar
import weakref

from .media_engine_seek import SeekCore
from .media_engine_clock import _identity

logger = logging.getLogger(__name__)
_CALLBACK_ERRORS = (AttributeError, OSError, ReferenceError, RuntimeError, TypeError, ValueError)
_Notify = TypeVar('_Notify')


class EventAdapter(Protocol):
    """Only current-owner state and the existing event forwarder are needed."""

    _core: SeekCore
    _closed: bool
    _shutdown_requested: bool

    def on_media_engine_event(self, event: int, param1: int, param2: int) -> None: ...


class BoundSeekEvents:
    """Notify lifetime anchor with weak owners; never retain native pointers."""

    def __init__(self, core: SeekCore, adapter: EventAdapter, generation: int) -> None:
        if type(generation) is not int or generation < 1:
            raise ValueError('Invalid callback engine generation')
        self._core = weakref.ref(core)
        self._adapter = weakref.ref(adapter)
        self._generation = generation

    def on_media_engine_event(self, event: int, param1: int, param2: int) -> None:
        """Stamp current seek at ingress, then preserve normal event/error pumping."""
        core, adapter = self._core(), self._adapter()
        if core is None or adapter is None:
            return
        try:
            if adapter._core is not core or adapter._closed or adapter._shutdown_requested:
                return
            with core._state_lock:
                generation, closing = core._engine_generation, core._shutdown_requested
                engine_present = core._media_engine is not None
            # Initial notifications during CreateInstance may precede assignment.
            # They may be forwarded, but cannot confirm a not-yet-started seek.
            if closing or generation not in (self._generation - 1, self._generation):
                return
            if generation == self._generation and not engine_present:
                return
            if generation == self._generation - 1 and engine_present:
                return
            if event in (16, 17) and generation == self._generation:
                self._observe(core, event)
            adapter.on_media_engine_event(event, param1, param2)
        except _CALLBACK_ERRORS:
            # Native callback boundary: discard malformed/closing observations;
            # never invent completion, query COM, or allow Python errors into ABI.
            logger.warning('Native seek notification could not be recorded.', exc_info=True)

    def _observe(self, core: SeekCore, event: int) -> None:
        identity = _identity(core)
        if not identity.readable or identity.generation != self._generation:
            return
        operation = core._seek_slot.current()
        if operation is not None:
            operation.observe_event(event, identity.generation, core._seek_slot.epoch, identity.source)


def create_bound_notify(core: SeekCore, adapter: EventAdapter,
                        factory: Callable[[BoundSeekEvents], _Notify]) -> _Notify:
    """Keep the proxy alive as long as its COM notify handler, not as long as core.

    Dynamic attribute assignment is restricted to the native callback object boundary.
    A factory failure/NULL handler is an error, not a legacy unbound fallback.
    """
    with core._state_lock:
        generation = core._engine_generation + 1
    owner = BoundSeekEvents(core, adapter, generation)
    handler = factory(owner)
    if handler is None:
        raise RuntimeError('Native callback factory returned no handler')
    setattr(handler, '_wavehelm_seek_event_owner', owner)
    return handler
