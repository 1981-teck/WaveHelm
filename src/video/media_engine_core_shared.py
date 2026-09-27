"""Shared typed cold control-plane contracts for MediaEngineCore behavior.

Protocol declarations describe the existing dynamically attached core API; they
are not runtime implementations. Playback, timed-text and stream work runs on the
COM thread. The state lock only protects small state snapshots, not COM calls.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Callable, Protocol, TypeVar


class MediaEngineError(RuntimeError):
    """A MediaEngineCore operation failed."""


CORE_PLAYBACK_EXCEPTIONS = (AttributeError, OSError, ReferenceError, RuntimeError, TypeError, ValueError)

CONVERSION_EXCEPTIONS = (TypeError, ValueError, OverflowError)

QUERY_EXCEPTIONS = CORE_PLAYBACK_EXCEPTIONS + CONVERSION_EXCEPTIONS


_Result = TypeVar("_Result")


class ComCallAdapter(Protocol):
    """Synchronous typed result transport to the owned COM thread."""

    def call_on_com_thread(self, name: str, callback: Callable[[], _Result]) -> _Result: ...


class CoreState(Protocol):
    """State shared by cold query/selection behavior, owned by MediaEngineCore."""

    _state_lock: AbstractContextManager[object]
    _shutdown_requested: bool
    _media_engine: object | None

    def _adapter_ref(self) -> ComCallAdapter | None: ...


class AudioStreamCore(CoreState, Protocol):
    """The explicit core methods consumed by the audio selection cluster."""

    def _get_stream_selection_on_com_thread(self, index: int) -> bool: ...
    def _set_stream_selection_on_com_thread(self, index: int, selected: bool) -> None: ...
    def _apply_stream_selections_on_com_thread(self) -> None: ...
    def _get_number_of_streams_on_com_thread(self) -> int: ...
