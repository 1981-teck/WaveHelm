from __future__ import annotations

from typing import Any, Callable

SignalHandler = Callable[..., Any]
SIGNAL_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)


class WxSignal:
    """Small signal helper used while the wx migration shell is incomplete."""

    def __init__(self) -> None:
        self._handlers: list[SignalHandler] = []

    def connect(self, handler: SignalHandler) -> None:
        if not callable(handler):
            raise TypeError("Signal handler must be callable.")
        if handler not in self._handlers:
            self._handlers.append(handler)

    def disconnect(self, handler: SignalHandler) -> None:
        try:
            self._handlers.remove(handler)
        except ValueError as exc:
            raise RuntimeError("Signal handler is not connected.") from exc

    def emit(self, *args: Any, **kwargs: Any) -> None:
        for handler in tuple(self._handlers):
            handler(*args, **kwargs)
